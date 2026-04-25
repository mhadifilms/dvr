"""``dvr snapshot`` — capture, restore, list, delete project snapshots."""

from __future__ import annotations

from typing import Annotated

import typer

from ... import snapshot as snap_mod
from ...resolve import Resolve
from .. import output

app = typer.Typer(name="snapshot", help="Save and restore point-in-time project snapshots.")


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


@app.command("save")
def save_snapshot(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Snapshot name. Defaults to '<project>@<UTC timestamp>'."),
    ] = None,
) -> None:
    """Capture the current project state to a snapshot on disk."""
    r = _resolve(ctx)
    snap = snap_mod.capture(r, name=name)
    path = snap_mod.save(snap)
    output.emit(
        {
            "name": snap.name,
            "project": snap.project,
            "captured_at": snap.captured_at,
            "path": str(path),
            "timeline_count": len(snap.data.get("timelines", [])),
            "settings_captured": len(snap.data.get("settings", {})),
        },
        fmt=ctx.obj["format"],
        headline=f"snapshot saved: {snap.name}",
    )


@app.command("list")
def list_snapshots_cmd(ctx: typer.Context) -> None:
    """List snapshots on disk, newest first."""
    rows = [
        {"name": s.name, "project": s.project, "captured_at": s.captured_at}
        for s in snap_mod.list_snapshots()
    ]
    output.emit(rows, fmt=ctx.obj["format"], headline="snapshots")


@app.command("show")
def show_snapshot(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Snapshot name.")],
) -> None:
    """Print a snapshot's contents."""
    snap = snap_mod.load(name)
    output.emit(snap.to_dict(), fmt=ctx.obj["format"], headline=f"snapshot: {snap.name}")


@app.command("restore")
def restore_snapshot(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Snapshot name to restore.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Preview without applying.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Re-apply a snapshot to the live Resolve state (load/create project, then reconcile)."""
    r = _resolve(ctx)
    snap = snap_mod.load(name)
    if not dry_run and not yes:
        typer.confirm(
            f"Restore snapshot {snap.name!r} to project {snap.project!r}? "
            "This will create the project if missing and ensure timelines/markers exist.",
            abort=True,
        )
    counts = snap_mod.restore(r, snap, dry_run=dry_run)
    output.emit(
        {
            "snapshot": snap.name,
            "project": snap.project,
            "dry_run": dry_run,
            **counts,
        },
        fmt=ctx.obj["format"],
        headline=f"restore: {snap.name}",
    )


@app.command("delete")
def delete_snapshot(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Snapshot name.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a snapshot from disk."""
    if not yes:
        typer.confirm(f"Delete snapshot {name!r}?", abort=True)
    snap_mod.delete(name)
    output.emit({"deleted": name}, fmt=ctx.obj["format"])
