"""``dvr clip`` — query, inspect, and bulk-mutate clips on the current timeline.

The standout feature here is ``--where``: a tiny safe expression
evaluator that filters the clip list with a Python-like syntax::

    dvr clip ls --where "track_index == 2 and duration > 24"
    dvr clip set --where "track_index == 2" pan=0.25 opacity=80
    dvr clip mark --where "duration < 12" --color Red --name "too short"

Variables in scope: ``name`` (str), ``track_type`` (str),
``track_index`` (int), ``start`` (int), ``end`` (int), ``duration``
(int), ``enabled`` (bool).
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Annotated, Any

import typer

from ... import errors, schema
from ...resolve import Resolve
from ...timeline import TimelineItem
from .. import output
from ..session import resolve_from_ctx as _resolve

app = typer.Typer(name="clip", help="Query and bulk-mutate clips on the current timeline.")


# ---------------------------------------------------------------------------
# Safe `--where` expression evaluator
# ---------------------------------------------------------------------------

_BIN_OPS: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_BOOL_OPS: dict[type[ast.AST], Callable[[Any], bool]] = {ast.And: all, ast.Or: any}

_UNARY_OPS: dict[type[ast.AST], Callable[[Any], Any]] = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise errors.DvrError(
                f"Unknown variable in --where: {node.id!r}.",
                fix="Variables: name, track_type, track_index, start, end, duration, enabled.",
            )
        return env[node.id]
    if isinstance(node, ast.BoolOp):
        op_fn = _BOOL_OPS[type(node.op)]
        return op_fn(_safe_eval(v, env) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand, env))
    if isinstance(node, ast.BinOp):
        return _BIN_OPS[type(node.op)](_safe_eval(node.left, env), _safe_eval(node.right, env))
    if isinstance(node, ast.Compare):
        left = _safe_eval(node.left, env)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _safe_eval(comparator, env)
            if not _BIN_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return type(node.elts and [_safe_eval(e, env) for e in node.elts])(
            _safe_eval(e, env) for e in node.elts
        )
    raise errors.DvrError(
        f"Unsupported syntax in --where: {ast.dump(node)}",
        fix="Use simple comparisons / boolean ops / arithmetic only.",
    )


def _compile_where(expression: str) -> Any:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise errors.DvrError(
            f"Invalid --where expression: {exc.msg}",
            state={"expression": expression},
        ) from exc

    def predicate(clip: TimelineItem) -> bool:
        env = {
            "name": clip.name,
            "track_type": clip.track_type,
            "track_index": clip.track_index,
            "start": clip.start,
            "end": clip.end,
            "duration": clip.duration,
            "enabled": clip.enabled,
        }
        return bool(_safe_eval(tree, env))

    return predicate


def _filter_clips(r: Resolve, where: str | None, track: str | None) -> list[TimelineItem]:
    tl = r.timeline.current
    if tl is None:
        raise errors.TimelineError(
            "No current timeline.",
            fix="Switch to or create a timeline first.",
        )
    clips = list(tl.clips(track))
    if where:
        predicate = _compile_where(where)
        clips = [c for c in clips if predicate(c)]
    return clips


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("ls")
def ls_cmd(
    ctx: typer.Context,
    where: Annotated[
        str | None,
        typer.Option("--where", "-w", help="Filter expression. See `dvr clip --help`."),
    ] = None,
    track: Annotated[
        str | None,
        typer.Option("--track", "-t", help="Track type filter: video|audio|subtitle."),
    ] = None,
) -> None:
    """List clips on the current timeline, optionally filtered."""
    r = _resolve(ctx)
    clips = _filter_clips(r, where, track)
    rows = [c.inspect() for c in clips]
    output.emit(rows, fmt=ctx.obj["format"], headline=f"{len(rows)} clip(s)")


@app.command("set")
def set_cmd(
    ctx: typer.Context,
    properties: Annotated[
        list[str],
        typer.Argument(help="One or more 'key=value' pairs (e.g. pan=0.25 opacity=80)."),
    ],
    where: Annotated[
        str | None,
        typer.Option("--where", "-w", help="Filter expression."),
    ] = None,
    track: Annotated[
        str | None,
        typer.Option("--track", "-t", help="Track type filter."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Print what would change without applying."),
    ] = False,
) -> None:
    """Bulk-set clip properties on filtered clips.

    Examples::

        dvr clip set --where "track_index==2" pan=0.25
        dvr clip set --where "duration<24" CompositeMode=Difference Opacity=70
    """
    r = _resolve(ctx)
    clips = _filter_clips(r, where, track)
    pairs: dict[str, Any] = {}
    for raw in properties:
        if "=" not in raw:
            raise typer.BadParameter(f"Expected 'key=value', got {raw!r}.")
        key, value = raw.split("=", 1)
        pairs[key.strip()] = _coerce(value.strip())
    normalized = schema.normalize_clip_properties(pairs)
    if dry_run:
        output.emit(
            {
                "would_update": [c.name for c in clips],
                "properties": normalized,
                "count": len(clips),
            },
            fmt=ctx.obj["format"],
        )
        return
    for clip in clips:
        clip.set_properties(normalized)
    output.emit(
        {"updated": len(clips), "properties": normalized},
        fmt=ctx.obj["format"],
    )


@app.command("transform")
def transform_cmd(
    ctx: typer.Context,
    where: Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")] = None,
    track: Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")] = None,
    pan: Annotated[float | None, typer.Option("--pan", help="Horizontal position.")] = None,
    tilt: Annotated[float | None, typer.Option("--tilt", help="Vertical position.")] = None,
    zoom: Annotated[
        float | None,
        typer.Option("--zoom", help="Set ZoomX and ZoomY together."),
    ] = None,
    zoom_x: Annotated[float | None, typer.Option("--zoom-x", help="Horizontal zoom.")] = None,
    zoom_y: Annotated[float | None, typer.Option("--zoom-y", help="Vertical zoom.")] = None,
    rotation: Annotated[float | None, typer.Option("--rotation", help="Rotation angle.")] = None,
    anchor_x: Annotated[float | None, typer.Option("--anchor-x", help="Anchor point X.")] = None,
    anchor_y: Annotated[float | None, typer.Option("--anchor-y", help="Anchor point Y.")] = None,
    pitch: Annotated[float | None, typer.Option("--pitch", help="3D pitch.")] = None,
    yaw: Annotated[float | None, typer.Option("--yaw", help="3D yaw.")] = None,
    flip_x: Annotated[
        bool | None, typer.Option("--flip-x/--no-flip-x", help="Flip horizontally.")
    ] = None,
    flip_y: Annotated[
        bool | None, typer.Option("--flip-y/--no-flip-y", help="Flip vertically.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Set transform properties on filtered timeline clips."""
    props = _drop_none(
        {
            "pan": pan,
            "tilt": tilt,
            "zoom": zoom,
            "zoom_x": zoom_x,
            "zoom_y": zoom_y,
            "rotation": rotation,
            "anchor_x": anchor_x,
            "anchor_y": anchor_y,
            "pitch": pitch,
            "yaw": yaw,
            "flip_x": flip_x,
            "flip_y": flip_y,
        }
    )
    _apply_properties(ctx, props, where=where, track=track, dry_run=dry_run)


@app.command("crop")
def crop_cmd(
    ctx: typer.Context,
    where: Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")] = None,
    track: Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")] = None,
    left: Annotated[float | None, typer.Option("--left", help="Left crop in pixels.")] = None,
    right: Annotated[float | None, typer.Option("--right", help="Right crop in pixels.")] = None,
    top: Annotated[float | None, typer.Option("--top", help="Top crop in pixels.")] = None,
    bottom: Annotated[float | None, typer.Option("--bottom", help="Bottom crop in pixels.")] = None,
    softness: Annotated[float | None, typer.Option("--softness", help="Crop softness.")] = None,
    retain: Annotated[
        bool | None,
        typer.Option("--retain/--no-retain", help="Retain image position."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Set crop properties on filtered timeline clips."""
    props = _drop_none(
        {
            "crop_left": left,
            "crop_right": right,
            "crop_top": top,
            "crop_bottom": bottom,
            "crop_softness": softness,
            "crop_retain": retain,
        }
    )
    _apply_properties(ctx, props, where=where, track=track, dry_run=dry_run)


@app.command("composite")
def composite_cmd(
    ctx: typer.Context,
    where: Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")] = None,
    track: Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")] = None,
    opacity: Annotated[float | None, typer.Option("--opacity", help="Opacity 0..100.")] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Composite/blend mode name or integer constant."),
    ] = None,
    distortion: Annotated[
        float | None, typer.Option("--distortion", help="Distortion -1..1.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Set composite properties on filtered timeline clips."""
    props = _drop_none({"opacity": opacity, "composite_mode": mode, "distortion": distortion})
    _apply_properties(ctx, props, where=where, track=track, dry_run=dry_run)


@app.command("retime")
def retime_cmd(
    ctx: typer.Context,
    where: Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")] = None,
    track: Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")] = None,
    process: Annotated[
        str | None,
        typer.Option("--process", help="UseProject, Nearest, FrameBlend, or OpticalFlow."),
    ] = None,
    motion_estimation: Annotated[
        str | None,
        typer.Option("--motion-estimation", help="Motion estimation enum name or integer."),
    ] = None,
    scaling_mode: Annotated[
        str | None,
        typer.Option("--scaling", help="UseProject, Crop, Fit, Fill, or Stretch."),
    ] = None,
    resize_filter: Annotated[
        str | None,
        typer.Option("--resize-filter", help="Resize filter enum name or integer."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Set retime/scaling quality properties on filtered clips."""
    props = _drop_none(
        {
            "retime_process": process,
            "motion_estimation": motion_estimation,
            "scaling": scaling_mode,
            "resize_filter": resize_filter,
        }
    )
    _apply_properties(ctx, props, where=where, track=track, dry_run=dry_run)


@app.command("reset")
def reset_cmd(
    ctx: typer.Context,
    groups: Annotated[
        list[str] | None,
        typer.Argument(
            help="Property groups to reset: transform, crop, composite, retime, scaling."
        ),
    ] = None,
    where: Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")] = None,
    track: Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Reset documented editing properties on filtered timeline clips."""
    props = schema.reset_clip_properties(groups)
    _apply_properties(ctx, props, where=where, track=track, dry_run=dry_run)


@app.command("capabilities")
def capabilities_cmd(ctx: typer.Context) -> None:
    """Describe Resolve timeline-item editing capabilities exposed by dvr."""
    output.emit(schema.clip_property_capabilities(), fmt=ctx.obj["format"])


@app.command("text")
def text_cmd(
    ctx: typer.Context,
    where: Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")] = None,
    track: Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="The displayed text string.")] = None,
    font: Annotated[
        str | None, typer.Option("--font", help="Font family, e.g. 'Open Sans'.")
    ] = None,
    style: Annotated[
        str | None, typer.Option("--style", help="Font style, e.g. Regular, Bold, Italic.")
    ] = None,
    size: Annotated[float | None, typer.Option("--size", help="Relative size (~0.05-0.2).")] = None,
    color: Annotated[
        str | None, typer.Option("--color", help="Hex (#ffcc00), name (white), or r,g,b.")
    ] = None,
    opacity: Annotated[float | None, typer.Option("--opacity", help="Text alpha, 0..1.")] = None,
    tracking: Annotated[float | None, typer.Option("--tracking", help="Letter spacing.")] = None,
    line_spacing: Annotated[
        float | None, typer.Option("--line-spacing", help="Line spacing.")
    ] = None,
    pos_x: Annotated[float | None, typer.Option("--x", help="Layout center X (0..1).")] = None,
    pos_y: Annotated[float | None, typer.Option("--y", help="Layout center Y (0..1).")] = None,
    align: Annotated[
        str | None, typer.Option("--align", help="Horizontal anchor: left|center|right.")
    ] = None,
    vertical_align: Annotated[
        str | None, typer.Option("--valign", help="Vertical anchor: top|center|bottom.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
) -> None:
    """Customize Text+ content/styling on filtered timeline clips.

    Only clips carrying a Fusion Text+ tool are updated; others are skipped
    and reported. Defaults to video clips when no ``--track`` is given.

    Examples::

        dvr clip text --where "name=='Text+'" --text "HELLO" --color "#ffcc00"
        dvr clip text -t video --font "Open Sans" --size 0.12 --align center
    """
    r = _resolve(ctx)
    clips = _filter_clips(r, where, track or "video")
    position = (pos_x, pos_y) if pos_x is not None and pos_y is not None else None
    color_value = _parse_color_option(color)
    fields = _drop_none(
        {
            "font": font,
            "style": style,
            "size": size,
            "color": color_value,
            "opacity": opacity,
            "tracking": tracking,
            "line_spacing": line_spacing,
            "position": position,
            "align": align,
            "vertical_align": vertical_align,
        }
    )
    if text is not None:
        fields["text"] = text
    if dry_run:
        output.emit(
            {"would_update": [c.name for c in clips], "fields": fields, "count": len(clips)},
            fmt=ctx.obj["format"],
        )
        return
    updated: list[str] = []
    skipped: list[dict[str, str]] = []
    for clip in clips:
        try:
            clip.text.set(**fields)
            updated.append(clip.name)
        except errors.FusionError as exc:
            skipped.append({"clip": clip.name, "reason": str(exc)})
    output.emit(
        {"updated": updated, "skipped": skipped, "count": len(updated)},
        fmt=ctx.obj["format"],
    )


def _parse_color_option(color: str | None) -> Any | None:
    """Allow ``--color`` to be a hex/name string or comma-separated r,g,b[,a]."""
    if color is None:
        return None
    if "," in color:
        parts = [p.strip() for p in color.split(",") if p.strip()]
        return [float(p) for p in parts]
    return color


@app.command("mark")
def mark_cmd(
    ctx: typer.Context,
    where: Annotated[
        str | None,
        typer.Option("--where", "-w", help="Filter expression."),
    ] = None,
    track: Annotated[
        str | None,
        typer.Option("--track", "-t", help="Track type filter."),
    ] = None,
    color: Annotated[
        str, typer.Option("--color", help="Marker color (e.g. Red, Blue, Green).")
    ] = "Blue",
    name: Annotated[str, typer.Option("--name", help="Marker name.")] = "",
    note: Annotated[str, typer.Option("--note", help="Marker note.")] = "",
    duration: Annotated[int, typer.Option("--duration", help="Marker duration.")] = 1,
) -> None:
    """Add a marker on each filtered clip."""
    r = _resolve(ctx)
    clips = _filter_clips(r, where, track)
    for clip in clips:
        clip.add_marker(color=color, name=name, note=note, duration=duration)
    output.emit({"marked": len(clips), "color": color}, fmt=ctx.obj["format"])


@app.command("inspect")
def inspect_cmd(
    ctx: typer.Context,
    where: Annotated[
        str | None,
        typer.Option("--where", "-w", help="Filter expression."),
    ] = None,
    track: Annotated[
        str | None,
        typer.Option("--track", "-t", help="Track type filter."),
    ] = None,
) -> None:
    """Print the full inspection snapshot for each filtered clip."""
    r = _resolve(ctx)
    clips = _filter_clips(r, where, track)
    rows = [c.inspect() for c in clips]
    output.emit(rows, fmt=ctx.obj["format"], headline=f"{len(rows)} clip(s)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce(value: str) -> Any:
    """Best-effort coerce a CLI string to int / float / bool / passthrough."""
    lower = value.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _drop_none(properties: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in properties.items() if value is not None}


def _apply_properties(
    ctx: typer.Context,
    properties: dict[str, Any],
    *,
    where: str | None,
    track: str | None,
    dry_run: bool,
) -> None:
    normalized = schema.normalize_clip_properties(properties)
    r = _resolve(ctx)
    clips = _filter_clips(r, where, track)
    if dry_run:
        output.emit(
            {
                "would_update": [c.name for c in clips],
                "properties": normalized,
                "count": len(clips),
            },
            fmt=ctx.obj["format"],
        )
        return
    for clip in clips:
        clip.set_properties(normalized)
    output.emit({"updated": len(clips), "properties": normalized}, fmt=ctx.obj["format"])
