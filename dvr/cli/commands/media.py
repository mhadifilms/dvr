"""``dvr media`` sub-commands."""

from __future__ import annotations

from typing import Annotated

import typer

from ...resolve import Resolve
from .. import output

app = typer.Typer(name="media", help="Media pool: bins, assets, import, relink, proxy, audio sync.")


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


@app.command("inspect")
def inspect_pool(ctx: typer.Context) -> None:
    """Inspect the current project's media pool."""
    r = _resolve(ctx)
    project = r.project.current
    if project is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    output.emit(project.media.inspect(), fmt=ctx.obj["format"], headline="media pool")


@app.command("bins")
def bins(ctx: typer.Context) -> None:
    """List bins in the current project's media pool."""
    r = _resolve(ctx)
    project = r.project.current
    if project is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    rows = [b.inspect() for b in project.media.root.subbins()]
    output.emit(rows, fmt=ctx.obj["format"], headline="bins")


@app.command("ls")
def ls_bin(
    ctx: typer.Context,
    bin: Annotated[
        str | None,
        typer.Argument(help="Bin name to list. Defaults to the root bin."),
    ] = None,
) -> None:
    """List assets in a bin."""
    r = _resolve(ctx)
    project = r.project.current
    if project is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    target = project.media._find_bin(bin) if bin else project.media.root
    rows = [a.inspect() for a in target.assets()]
    output.emit(rows, fmt=ctx.obj["format"], headline=f"assets in {target.name}")


@app.command("mkbin")
def mkbin(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Bin name.")],
    parent: Annotated[
        str | None,
        typer.Option("--parent", help="Parent bin (default: root)."),
    ] = None,
) -> None:
    """Create (or get-or-create) a bin."""
    r = _resolve(ctx)
    project = r.project.current
    if project is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    parent_bin = project.media._find_bin(parent) if parent else project.media.root
    bin_obj = project.media.ensure_bin(name, parent=parent_bin)
    output.emit(bin_obj.inspect(), fmt=ctx.obj["format"], headline=f"bin: {bin_obj.name}")


@app.command("import")
def import_files(
    ctx: typer.Context,
    paths: Annotated[list[str], typer.Argument(help="One or more file paths to import.")],
    bin: Annotated[
        str | None,
        typer.Option("--bin", "-b", help="Target bin (default: current)."),
    ] = None,
) -> None:
    """Import media files into the pool."""
    r = _resolve(ctx)
    project = r.project.current
    if project is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    target_bin = project.media._find_bin(bin) if bin else None
    assets = project.media.import_(paths, bin=target_bin)
    rows = [a.inspect() for a in assets]
    output.emit(rows, fmt=ctx.obj["format"], headline=f"imported {len(rows)}")


@app.command("relink")
def relink(
    ctx: typer.Context,
    folder: Annotated[
        str,
        typer.Argument(help="Folder containing replacement media."),
    ],
    bin: Annotated[
        str | None,
        typer.Option("--bin", "-b", help="Bin whose assets to relink (default: root)."),
    ] = None,
) -> None:
    """Relink assets in a bin to new files in a folder."""
    r = _resolve(ctx)
    project = r.project.current
    if project is None:
        typer.echo("No project is currently loaded.", err=True)
        raise typer.Exit(1)
    target = project.media._find_bin(bin) if bin else project.media.root
    project.media.relink(target.assets(), folder)
    output.emit(
        {"relinked": len(target.assets()), "folder": folder, "bin": target.name},
        fmt=ctx.obj["format"],
    )


@app.command("storage")
def storage(
    ctx: typer.Context,
    path: Annotated[
        str | None,
        typer.Argument(help="Path to list (default: mounted volumes)."),
    ] = None,
) -> None:
    """List mounted volumes (no path) or files/folders under a path."""
    r = _resolve(ctx)
    if path is None:
        output.emit(
            [{"volume": v} for v in r.storage.volumes()],
            fmt=ctx.obj["format"],
            headline="volumes",
        )
        return
    rows = [{"name": p, "kind": "folder"} for p in r.storage.subfolders(path)]
    rows.extend({"name": f, "kind": "file"} for f in r.storage.files(path))
    output.emit(rows, fmt=ctx.obj["format"], headline=path)
