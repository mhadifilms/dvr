"""``dvr project`` sub-commands."""

from __future__ import annotations

from typing import Annotated

import typer

from ...resolve import Resolve
from .. import output

app = typer.Typer(
    name="project", help="Project operations: list, ensure, load, create, delete, archive."
)


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


@app.command("list")
def list_projects(ctx: typer.Context) -> None:
    """List projects in the current PM folder."""
    r = _resolve(ctx)
    rows = [{"name": n} for n in r.project.list()]
    output.emit(rows, fmt=ctx.obj["format"], headline="projects")


@app.command("current")
def current(ctx: typer.Context) -> None:
    """Inspect the currently loaded project."""
    r = _resolve(ctx)
    proj = r.project.current
    if proj is None:
        output.emit({"current": None}, fmt=ctx.obj["format"])
        return
    output.emit(proj.inspect(), fmt=ctx.obj["format"], headline=proj.name)


@app.command("ensure")
def ensure(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Project name to load-or-create.")],
) -> None:
    """Load the project if it exists, otherwise create it."""
    r = _resolve(ctx)
    proj = r.project.ensure(name)
    output.emit(proj.inspect(), fmt=ctx.obj["format"], headline=f"ensured: {proj.name}")


@app.command("create")
def create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Project name (must be unique).")],
) -> None:
    """Create a new project."""
    r = _resolve(ctx)
    proj = r.project.create(name)
    output.emit(proj.inspect(), fmt=ctx.obj["format"], headline=f"created: {proj.name}")


@app.command("load")
def load(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Project name to load.")],
) -> None:
    """Load an existing project."""
    r = _resolve(ctx)
    proj = r.project.load(name)
    output.emit(proj.inspect(), fmt=ctx.obj["format"], headline=f"loaded: {proj.name}")


@app.command("delete")
def delete(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Project name to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete a project (must be closed first)."""
    if not yes:
        typer.confirm(f"Really delete project {name!r}?", abort=True)
    r = _resolve(ctx)
    r.project.delete(name)
    output.emit({"deleted": name}, fmt=ctx.obj["format"])


@app.command("save")
def save(ctx: typer.Context) -> None:
    """Save the currently loaded project."""
    r = _resolve(ctx)
    proj = r.project.current
    if proj is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    proj.save()
    output.emit({"saved": proj.name}, fmt=ctx.obj["format"])


@app.command("export")
def export(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Project name to export.")],
    file: Annotated[str, typer.Argument(help="Output .drp path.")],
    no_assets: Annotated[
        bool, typer.Option("--no-assets", help="Exclude stills and LUTs.")
    ] = False,
) -> None:
    """Export a project to a .drp file."""
    r = _resolve(ctx)
    r.project.export(name, file, with_stills_and_luts=not no_assets)
    output.emit({"exported": name, "path": file}, fmt=ctx.obj["format"])


@app.command("import")
def import_(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="Input .drp path.")],
    name: Annotated[
        str | None, typer.Option("--name", help="Override the imported project name.")
    ] = None,
) -> None:
    """Import a project from a .drp file."""
    r = _resolve(ctx)
    proj = r.project.import_(file, name=name)
    output.emit(proj.inspect(), fmt=ctx.obj["format"], headline=f"imported: {proj.name}")


def _current(ctx: typer.Context):  # type: ignore[no-untyped-def]
    r = _resolve(ctx)
    proj = r.project.current
    if proj is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    return proj


@app.command("reset-intellisearch")
def reset_intellisearch(ctx: typer.Context) -> None:
    """Clear Intellisearch analysis data for the current project (Resolve 21+)."""
    proj = _current(ctx)
    proj.reset_intellisearch_analysis()
    output.emit({"reset_intellisearch": proj.name}, fmt=ctx.obj["format"])


@app.command("generate-speech")
def generate_speech(
    ctx: typer.Context,
    text: Annotated[str, typer.Option("--text", help="Text to synthesize.")],
    timecode: Annotated[
        str, typer.Option("--timecode", help="Timecode to place the clip at (if added).")
    ] = "01:00:00:00",
    voice: Annotated[
        str | None, typer.Option("--voice", help="Voice model, e.g. 'Female 1'.")
    ] = None,
    speed: Annotated[
        float | None, typer.Option("--speed", help="Speech speed multiplier (1.0 = normal).")
    ] = None,
    pitch: Annotated[float | None, typer.Option("--pitch", help="Voice pitch adjustment.")] = None,
    filename: Annotated[
        str | None, typer.Option("--filename", help="Name for the generated audio clip.")
    ] = None,
    track: Annotated[
        int | None, typer.Option("--track", help="Audio track index when adding to timeline.")
    ] = None,
    add_to_timeline: Annotated[
        bool,
        typer.Option(
            "--add-to-timeline/--no-add-to-timeline", help="Place the clip on the timeline."
        ),
    ] = True,
) -> None:
    """Generate a text-to-speech audio clip (Resolve 21+, Studio)."""
    proj = _current(ctx)
    settings: dict[str, object] = {"TextInput": text, "AddToTimeline": add_to_timeline}
    if voice:
        settings["VoiceModel"] = voice
    if speed is not None:
        settings["Speed"] = speed
    if pitch is not None:
        settings["Pitch"] = pitch
    if filename:
        settings["Filename"] = filename
    if track is not None:
        settings["AudioTrack"] = track
    clip = proj.generate_speech(settings, timecode)
    output.emit(
        {"generated": clip.name, "timecode": timecode, "added_to_timeline": add_to_timeline},
        fmt=ctx.obj["format"],
    )
