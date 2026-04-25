"""``dvr diff`` — compare timelines, snapshots, and specs."""

from __future__ import annotations

from typing import Annotated

import typer

from ... import diff as diff_mod
from ... import snapshot as snap_mod
from ... import spec as spec_mod
from ...resolve import Resolve
from .. import output

app = typer.Typer(name="diff", help="Compare timelines, snapshots, and specs.")


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


@app.command("timelines")
def diff_timelines(
    ctx: typer.Context,
    a: Annotated[str, typer.Argument(help="Left timeline name.")],
    b: Annotated[str, typer.Argument(help="Right timeline name.")],
) -> None:
    """Diff two timelines in the current project."""
    r = _resolve(ctx)
    left = r.timeline.get(a)
    right = r.timeline.get(b)
    result = diff_mod.compare_timelines(left, right)
    output.emit(result.to_dict(), fmt=ctx.obj["format"], headline=f"diff {a} vs {b}")


@app.command("spec")
def diff_spec(
    ctx: typer.Context,
    spec_file: Annotated[str, typer.Argument(help="Path to a YAML or JSON spec.")],
) -> None:
    """Diff the live state against a spec file."""
    r = _resolve(ctx)
    parsed = spec_mod.load_spec(spec_file)
    result = diff_mod.compare_to_spec(r, parsed)
    output.emit(result.to_dict(), fmt=ctx.obj["format"], headline=f"diff live vs {spec_file}")


@app.command("snapshot")
def diff_snapshot(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Snapshot name to compare against the live state.")],
) -> None:
    """Diff a saved snapshot against the live project state."""
    r = _resolve(ctx)
    snap = snap_mod.load(name)
    live = snap_mod.capture(r, name=f"_live_for_diff_{snap.name}")
    result = diff_mod.compare(
        snap.data,
        live.data,
        left_label=f"snapshot:{snap.name}",
        right_label="live",
    )
    output.emit(result.to_dict(), fmt=ctx.obj["format"], headline=f"diff {name} vs live")
