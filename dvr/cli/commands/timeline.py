"""``dvr timeline`` sub-commands."""

from __future__ import annotations

from typing import Annotated

import typer

from ...resolve import Resolve
from .. import output

app = typer.Typer(
    name="timeline", help="Timeline operations: list, inspect, ensure, current, switch."
)


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


@app.command("list")
def list_timelines(ctx: typer.Context) -> None:
    """List timelines in the currently loaded project."""
    r = _resolve(ctx)
    rows = [
        {"name": tl.name, "fps": tl.fps, "duration": tl.duration_frames} for tl in r.timeline.list()
    ]
    output.emit(rows, fmt=ctx.obj["format"], headline="timelines")


@app.command("current")
def current(ctx: typer.Context) -> None:
    """Inspect the currently active timeline."""
    r = _resolve(ctx)
    tl = r.timeline.current
    if tl is None:
        output.emit({"current": None}, fmt=ctx.obj["format"])
        return
    output.emit(tl.inspect(), fmt=ctx.obj["format"], headline=tl.name)


@app.command("inspect")
def inspect_timeline(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Timeline name; defaults to the current timeline."),
    ] = None,
) -> None:
    """Return a structured snapshot of a timeline."""
    r = _resolve(ctx)
    tl = r.timeline.get(name) if name else r.timeline.current
    if tl is None:
        typer.echo("No timeline is currently loaded.", err=True)
        raise typer.Exit(1)
    output.emit(tl.inspect(), fmt=ctx.obj["format"], headline=tl.name)


@app.command("ensure")
def ensure(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Timeline name to load-or-create.")],
) -> None:
    """Get-or-create a timeline by name."""
    r = _resolve(ctx)
    tl = r.timeline.ensure(name)
    output.emit(tl.inspect(), fmt=ctx.obj["format"], headline=f"ensured: {tl.name}")


@app.command("create")
def create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Timeline name (must be unique).")],
) -> None:
    """Create an empty timeline."""
    r = _resolve(ctx)
    tl = r.timeline.create(name)
    output.emit(tl.inspect(), fmt=ctx.obj["format"], headline=f"created: {tl.name}")


@app.command("switch")
def switch(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Timeline name to set as current.")],
) -> None:
    """Set a timeline as the current one."""
    r = _resolve(ctx)
    tl = r.timeline.set_current(name)
    output.emit({"current": tl.name}, fmt=ctx.obj["format"])


@app.command("delete")
def delete(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Timeline name to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Delete a timeline."""
    if not yes:
        typer.confirm(f"Really delete timeline {name!r}?", abort=True)
    r = _resolve(ctx)
    r.timeline.delete(name)
    output.emit({"deleted": name}, fmt=ctx.obj["format"])
