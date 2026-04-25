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

from ... import errors
from ...resolve import Resolve
from ...timeline import TimelineItem
from .. import output

app = typer.Typer(name="clip", help="Query and bulk-mutate clips on the current timeline.")


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


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
    pairs = []
    for raw in properties:
        if "=" not in raw:
            raise typer.BadParameter(f"Expected 'key=value', got {raw!r}.")
        key, value = raw.split("=", 1)
        pairs.append((key.strip(), _coerce(value.strip())))
    if dry_run:
        output.emit(
            {
                "would_update": [c.name for c in clips],
                "properties": dict(pairs),
                "count": len(clips),
            },
            fmt=ctx.obj["format"],
        )
        return
    for clip in clips:
        for key, value in pairs:
            clip.set_property(key, value)
    output.emit(
        {"updated": len(clips), "properties": dict(pairs)},
        fmt=ctx.obj["format"],
    )


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
