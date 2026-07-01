"""``dvr project`` sub-commands."""

from __future__ import annotations

from typing import Annotated

import typer

from .. import output
from ..session import resolve_from_ctx as _resolve

app = typer.Typer(
    name="project", help="Project operations: list, ensure, load, create, delete, archive."
)


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


@app.command("color-groups")
def color_groups(
    ctx: typer.Context,
    add: Annotated[str | None, typer.Option("--add", help="Create a color group by name.")] = None,
    delete: Annotated[
        str | None, typer.Option("--delete", help="Delete a color group by name.")
    ] = None,
) -> None:
    """List, create, or delete project color groups."""
    proj = _current(ctx)
    if add:
        group = proj.add_color_group(add)
        output.emit({"created": group.name}, fmt=ctx.obj["format"])
        return
    if delete:
        match = next((g for g in proj.color_groups() if g.name == delete), None)
        if match is None:
            typer.echo(f"No color group named {delete!r}.", err=True)
            raise typer.Exit(1)
        proj.delete_color_group(match)
        output.emit({"deleted": delete}, fmt=ctx.obj["format"])
        return
    output.emit(
        [{"name": g.name} for g in proj.color_groups()],
        fmt=ctx.obj["format"],
        headline="color groups",
    )


@app.command("export-still")
def export_still(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="Output image path for the current Color frame.")],
) -> None:
    """Export the current Color-page frame as a still (Resolve 18.5+)."""
    proj = _current(ctx)
    proj.export_current_frame_as_still(file)
    output.emit({"exported": file}, fmt=ctx.obj["format"])


@app.command("quick-export")
def quick_export(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="Output directory/path.")],
    preset: Annotated[str, typer.Option("--preset", help="Quick Export preset name.")],
) -> None:
    """Render the current timeline with a Quick Export preset (Resolve 18.6+)."""
    proj = _current(ctx)
    result = proj.quick_export(file, preset)
    output.emit({"quick_export": result, "preset": preset}, fmt=ctx.obj["format"])


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
