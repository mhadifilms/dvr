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


@app.command("add-title")
def add_title(
    ctx: typer.Context,
    title: Annotated[
        str, typer.Argument(help="Title/template name from Resolve's Titles list.")
    ] = "Text+",
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
    at: Annotated[
        str | None, typer.Option("--at", help="Timecode to place the title at (seeks first).")
    ] = None,
    fusion: Annotated[
        bool,
        typer.Option("--fusion/--standard", help="Fusion title (text-editable) vs standard title."),
    ] = True,
) -> None:
    """Insert a (Fusion) title on the current timeline and customize its text."""
    r = _resolve(ctx)
    tl = r.timeline.current
    if tl is None:
        typer.echo("No timeline is currently loaded.", err=True)
        raise typer.Exit(1)
    if at:
        tl.current_timecode = at
    position = (pos_x, pos_y) if pos_x is not None and pos_y is not None else None
    color_value = _parse_color_option(color)
    item = tl.insert_title(
        title,
        fusion=fusion,
        text=text,
        font=font,
        style=style,
        size=size,
        color=color_value,
        opacity=opacity,
        tracking=tracking,
        line_spacing=line_spacing,
        position=position,
        align=align,
        vertical_align=vertical_align,
    )
    output.emit(
        {"inserted": item.name, "title": title, "fusion": fusion, "at": at},
        fmt=ctx.obj["format"],
    )


@app.command("subtitles")
def subtitles(
    ctx: typer.Context,
    language: Annotated[str, typer.Option("--language", help="Language code or 'auto'.")] = "auto",
    chars_per_line: Annotated[
        int, typer.Option("--chars-per-line", help="Max characters per subtitle line.")
    ] = 42,
    line_break: Annotated[
        str, typer.Option("--line-break", help="Line break type, e.g. Auto.")
    ] = "Auto",
    preset: Annotated[
        str | None, typer.Option("--preset", help="Subtitle caption preset name.")
    ] = None,
) -> None:
    """Generate subtitles from the current timeline's audio (Whisper, Studio)."""
    r = _resolve(ctx)
    tl = r.timeline.current
    if tl is None:
        typer.echo("No timeline is currently loaded.", err=True)
        raise typer.Exit(1)
    tl.create_subtitles_from_audio(
        language=language,
        chars_per_line=chars_per_line,
        line_break_type=line_break,
        preset=preset,
    )
    output.emit({"timeline": tl.name, "subtitles_created": True}, fmt=ctx.obj["format"])


def _parse_color_option(color: str | None) -> object | None:
    """Allow ``--color`` to be a hex/name string or a comma-separated r,g,b[,a]."""
    if color is None:
        return None
    if "," in color:
        parts = [p.strip() for p in color.split(",") if p.strip()]
        return [float(p) for p in parts]
    return color
