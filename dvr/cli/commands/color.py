"""``dvr color`` — Color-page operations on filtered timeline clips.

Selection works exactly like ``dvr clip``: ``--where`` filters with the same
small expression language and ``--track`` narrows by track type.

    dvr color inspect --where "track_index == 1"
    dvr color cdl --slope 1.0 0.98 0.95 --where "name contains 'SH010'"
    dvr color lut set --node 2 /path/to/show.cube
    dvr color copy --source PLATE_v003
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ... import errors
from ...timeline import TimelineItem
from .. import output
from ..session import resolve_from_ctx as _resolve
from .clip import _filter_clips

app = typer.Typer(
    name="color",
    help="Color page: grades, CDL, node LUTs, versions, groups.",
    no_args_is_help=True,
)

lut_app = typer.Typer(name="lut", help="Per-node LUTs on graded clips.", no_args_is_help=True)
version_app = typer.Typer(name="version", help="Grade versions.", no_args_is_help=True)
app.add_typer(lut_app, name="lut")
app.add_typer(version_app, name="version")

_WhereOpt = Annotated[str | None, typer.Option("--where", "-w", help="Filter expression.")]
_TrackOpt = Annotated[str | None, typer.Option("--track", "-t", help="Track type filter.")]
_DryRunOpt = Annotated[bool, typer.Option("--dry-run", "-n", help="Preview without writing.")]


def _clips(ctx: typer.Context, where: str | None, track: str | None) -> list[TimelineItem]:
    clips = _filter_clips(_resolve(ctx), where, track or "video")
    if not clips:
        raise errors.ColorError(
            "No timeline clips matched the filter.",
            fix="Run `dvr clip ls --where ...` to see what matches.",
        )
    return clips


def _emit(ctx: typer.Context, payload: Any, headline: str | None = None) -> None:
    output.emit(payload, fmt=ctx.obj["format"], headline=headline)


@app.command("inspect")
def inspect_cmd(
    ctx: typer.Context,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
    layer: Annotated[int, typer.Option("--layer", help="Node graph layer.")] = 1,
) -> None:
    """Show node graph, grade versions, and color group for filtered clips."""
    rows = []
    for clip in _clips(ctx, where, track):
        ops = clip.color
        row: dict[str, Any] = {"clip": clip.name, "track_index": clip.track_index}
        try:
            row["graph"] = ops.graph(layer).inspect()
        except errors.DvrError as exc:
            row["graph"] = None
            row["graph_error"] = exc.message
        row["versions"] = ops.versions()
        group = ops.color_group()
        row["color_group"] = group.name if group else None
        rows.append(row)
    _emit(ctx, rows, f"{len(rows)} clip(s)")


@app.command("cdl")
def cdl_cmd(
    ctx: typer.Context,
    slope: Annotated[
        tuple[float, float, float] | None, typer.Option("--slope", help="RGB slope.")
    ] = None,
    offset: Annotated[
        tuple[float, float, float] | None, typer.Option("--offset", help="RGB offset.")
    ] = None,
    power: Annotated[
        tuple[float, float, float] | None, typer.Option("--power", help="RGB power.")
    ] = None,
    saturation: Annotated[float | None, typer.Option("--saturation")] = None,
    node: Annotated[int, typer.Option("--node", help="Node index.")] = 1,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Apply a CDL grade to a node on every filtered clip."""
    if slope is None and offset is None and power is None and saturation is None:
        raise errors.ColorError(
            "Nothing to apply.",
            fix="Pass at least one of --slope, --offset, --power, or --saturation.",
        )
    clips = _clips(ctx, where, track)
    payload = {
        "node_index": node,
        "slope": slope,
        "offset": offset,
        "power": power,
        "saturation": saturation,
    }
    if dry_run:
        _emit(ctx, {"dry_run": True, "clips": [c.name for c in clips], "cdl": payload})
        return
    for clip in clips:
        clip.color.set_cdl(
            node_index=node,
            slope=slope,
            offset=offset,
            power=power,
            saturation=saturation,
        )
    _emit(ctx, {"updated": len(clips), "clips": [c.name for c in clips], "cdl": payload})


@lut_app.command("get")
def lut_get_cmd(
    ctx: typer.Context,
    node: Annotated[int, typer.Option("--node")] = 1,
    layer: Annotated[int, typer.Option("--layer")] = 1,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
) -> None:
    """Read the LUT assigned to a color node."""
    rows = [
        {"clip": c.name, "node": node, "lut": c.color.graph(layer).get_lut(node)}
        for c in _clips(ctx, where, track)
    ]
    _emit(ctx, rows, f"{len(rows)} clip(s)")


@lut_app.command("set")
def lut_set_cmd(
    ctx: typer.Context,
    lut_path: Annotated[str, typer.Argument(help="LUT path as Resolve expects it.")],
    node: Annotated[int, typer.Option("--node")] = 1,
    layer: Annotated[int, typer.Option("--layer")] = 1,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Assign a LUT to a color node on every filtered clip."""
    clips = _clips(ctx, where, track)
    if dry_run:
        _emit(ctx, {"dry_run": True, "clips": [c.name for c in clips], "lut": lut_path})
        return
    for clip in clips:
        clip.color.graph(layer).set_lut(node, lut_path)
    _emit(ctx, {"updated": len(clips), "clips": [c.name for c in clips], "lut": lut_path})


@lut_app.command("export")
def lut_export_cmd(
    ctx: typer.Context,
    file_path: Annotated[str, typer.Argument(help="Destination LUT file.")],
    size: Annotated[str, typer.Option("--size", help="17, 33, 65, or vlt.")] = "33",
    where: _WhereOpt = None,
    track: _TrackOpt = None,
) -> None:
    """Export the first filtered clip's grade as a LUT."""
    clip = _clips(ctx, where, track)[0]
    clip.color.export_lut(file_path, size=size if size == "vlt" else int(size))
    _emit(ctx, {"clip": clip.name, "file_path": file_path, "size": size})


@version_app.command("ls")
def version_ls_cmd(
    ctx: typer.Context,
    remote: Annotated[bool, typer.Option("--remote", help="List remote versions.")] = False,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
) -> None:
    """List grade versions on filtered clips."""
    version_type = 1 if remote else 0
    rows = [
        {"clip": c.name, "versions": c.color.versions(version_type=version_type)}
        for c in _clips(ctx, where, track)
    ]
    _emit(ctx, rows, f"{len(rows)} clip(s)")


@version_app.command("add")
def version_add_cmd(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Version name.")],
    remote: Annotated[bool, typer.Option("--remote")] = False,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
) -> None:
    """Add a grade version to filtered clips."""
    clips = _clips(ctx, where, track)
    for clip in clips:
        clip.color.add_version(name, version_type=1 if remote else 0)
    _emit(ctx, {"added": name, "clips": [c.name for c in clips]})


@version_app.command("load")
def version_load_cmd(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Version name.")],
    remote: Annotated[bool, typer.Option("--remote")] = False,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
) -> None:
    """Load a grade version on filtered clips."""
    clips = _clips(ctx, where, track)
    for clip in clips:
        clip.color.load_version(name, version_type=1 if remote else 0)
    _emit(ctx, {"loaded": name, "clips": [c.name for c in clips]})


@version_app.command("delete")
def version_delete_cmd(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Version name.")],
    remote: Annotated[bool, typer.Option("--remote")] = False,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
) -> None:
    """Delete a grade version from filtered clips."""
    clips = _clips(ctx, where, track)
    for clip in clips:
        clip.color.delete_version(name, version_type=1 if remote else 0)
    _emit(ctx, {"deleted": name, "clips": [c.name for c in clips]})


@app.command("copy")
def copy_cmd(
    ctx: typer.Context,
    source: Annotated[
        str | None, typer.Option("--source", help="Source clip name. Defaults to the first match.")
    ] = None,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Copy one clip's grade onto every other filtered clip."""
    clips = _clips(ctx, where, track)
    if source:
        origin = next((c for c in clips if c.name == source), None)
        if origin is None:
            raise errors.ColorError(
                f"No filtered clip named {source!r}.",
                fix="Run `dvr clip ls` to see clip names, or widen --where.",
            )
    else:
        origin = clips[0]
    targets = [c for c in clips if c.raw is not origin.raw]
    if not targets:
        raise errors.ColorError(
            "The source is the only matched clip, so there is nothing to copy to.",
            fix="Widen --where, or pass --source to grade from a different clip.",
        )
    if dry_run:
        _emit(ctx, {"dry_run": True, "source": origin.name, "targets": [t.name for t in targets]})
        return
    origin.color.copy_grades_to(targets)
    _emit(
        ctx, {"source": origin.name, "updated": len(targets), "targets": [t.name for t in targets]}
    )


@app.command("reset")
def reset_cmd(
    ctx: typer.Context,
    layer: Annotated[int, typer.Option("--layer")] = 1,
    where: _WhereOpt = None,
    track: _TrackOpt = None,
    dry_run: _DryRunOpt = False,
) -> None:
    """Reset every color node on filtered clips to a neutral grade."""
    clips = _clips(ctx, where, track)
    if dry_run:
        _emit(ctx, {"dry_run": True, "clips": [c.name for c in clips]})
        return
    for clip in clips:
        clip.color.graph(layer).reset_all()
    _emit(ctx, {"reset": len(clips), "clips": [c.name for c in clips]})


@app.command("group")
def group_cmd(
    ctx: typer.Context,
    add: Annotated[str | None, typer.Option("--add", help="Create a color group.")] = None,
    delete: Annotated[str | None, typer.Option("--delete", help="Delete a color group.")] = None,
) -> None:
    """List color groups, or create/delete one."""
    r = _resolve(ctx)
    project = r.project.require_current()
    if add:
        _emit(ctx, {"created": project.add_color_group(add).name})
        return
    if delete:
        match = next((g for g in project.color_groups() if g.name == delete), None)
        if match is None:
            raise errors.ColorError(
                f"No color group named {delete!r}.",
                fix="Run `dvr color group` to list them.",
            )
        project.delete_color_group(match)
        _emit(ctx, {"deleted": delete})
        return
    _emit(ctx, [{"name": g.name} for g in project.color_groups()])
