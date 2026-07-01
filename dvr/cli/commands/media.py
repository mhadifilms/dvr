"""``dvr media`` sub-commands."""

from __future__ import annotations

from typing import Annotated

import typer

from ... import errors
from ...media import Clip, Folder, scan_media_files
from ...project import Project
from .. import output
from ..session import current_project as _current_project
from ..session import resolve_from_ctx as _resolve

app = typer.Typer(name="media", help="Media pool: bins, assets, import, relink, proxy, audio sync.")


def _ai_target(ctx: typer.Context, *, bin: str | None, clip: str | None) -> Clip | Folder:
    """Resolve the AI-operation target: a single clip (``--clip``) or a bin/folder.

    ``--clip`` takes precedence; otherwise the named bin (or the root
    folder) is used so the operation applies to the whole bin tree.
    """
    pool = _current_project(ctx).media
    if clip is not None:
        found = pool.find_clip(name=clip)
        if found is None:
            raise errors.MediaError(
                f"No clip named {clip!r} in the media pool.",
                fix="List clips with `dvr media ls` to find the exact name.",
                state={"requested": clip},
            )
        return found
    return pool.find_folder_path(bin) if bin else pool.root


@app.command("inspect")
def inspect_pool(ctx: typer.Context) -> None:
    """Inspect the current project's media pool."""
    project = _current_project(ctx)
    output.emit(project.media.inspect(), fmt=ctx.obj["format"], headline="media pool")


@app.command("export-metadata")
def export_metadata(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="Output CSV path.")],
) -> None:
    """Export metadata for every media-pool clip to a CSV file."""
    project: Project = _current_project(ctx)
    project.media.export_metadata(file)
    output.emit({"exported": file}, fmt=ctx.obj["format"])


@app.command("import-bin")
def import_bin(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="Path to a .drb bin file.")],
    source_clips: Annotated[
        str | None,
        typer.Option("--source-clips", help="Folder to search for source clips."),
    ] = None,
) -> None:
    """Import a media-pool bin from a .drb file (Resolve 18+)."""
    project = _current_project(ctx)
    project.media.import_folder_from_file(file, source_clips_path=source_clips or "")
    output.emit({"imported": file}, fmt=ctx.obj["format"])


@app.command("bins")
def bins(ctx: typer.Context) -> None:
    """List bins in the current project's media pool."""
    project = _current_project(ctx)
    rows = [b.inspect() for b in project.media.root.subbins()]
    output.emit(rows, fmt=ctx.obj["format"], headline="bins")


@app.command("ls")
def ls_bin(
    ctx: typer.Context,
    bin: Annotated[
        str | None,
        typer.Argument(help="Bin name or `A/B` path to list. Defaults to the root bin."),
    ] = None,
) -> None:
    """List assets in a bin."""
    pool = _current_project(ctx).media
    target = pool.find_folder_path(bin) if bin else pool.root
    rows = [a.inspect() for a in target.assets()]
    output.emit(rows, fmt=ctx.obj["format"], headline=f"assets in {target.name}")


@app.command("scan")
def scan(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="File or directory to scan for media files.")],
    recursive: Annotated[
        bool,
        typer.Option("--recursive/--no-recursive", help="Recurse into subdirectories."),
    ] = True,
    include_hidden: Annotated[
        bool,
        typer.Option("--include-hidden", help="Include hidden and AppleDouble (._*) files."),
    ] = False,
    max_files: Annotated[
        int,
        typer.Option("--max-files", help="Stop after this many matches.", min=1),
    ] = 10000,
) -> None:
    """Preview which media files an import would pick up (no Resolve needed)."""
    files = scan_media_files(
        path,
        recursive=recursive,
        include_hidden=include_hidden,
        max_files=max_files,
    )
    output.emit(files, fmt=ctx.obj["format"], headline=f"media files under {path}")


@app.command("mkbin")
def mkbin(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Bin name or `A/B` path to create.")],
    parent: Annotated[
        str | None,
        typer.Option("--parent", help="Parent bin (default: root)."),
    ] = None,
) -> None:
    """Create (or get-or-create) a bin. Accepts nested `A/B` paths."""
    pool = _current_project(ctx).media
    if parent:
        parent_bin = pool.find_folder_path(parent)
        bin_obj = pool.ensure_bin(name, parent=parent_bin)
    else:
        bin_obj = pool.ensure_folder_path(name)
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
    pool = _current_project(ctx).media
    target_bin = pool.ensure_folder_path(bin) if bin else None
    assets = pool.import_(paths, bin=target_bin)
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
    pool = _current_project(ctx).media
    target = pool.find_folder_path(bin) if bin else pool.root
    pool.relink(target.assets(), folder)
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


# ---------------------------------------------------------------------------
# AI / Studio operations (Resolve 21+). All target a bin (default: root) or a
# single clip via --clip. They raise a clear "requires Resolve 21" error on
# older builds rather than failing obscurely.
# ---------------------------------------------------------------------------


@app.command("transcribe")
def transcribe_cmd(
    ctx: typer.Context,
    bin: Annotated[
        str | None, typer.Argument(help="Bin to transcribe. Defaults to the root bin.")
    ] = None,
    clip: Annotated[
        str | None, typer.Option("--clip", help="Transcribe a single clip by name instead.")
    ] = None,
    speaker_detection: Annotated[
        bool | None,
        typer.Option(
            "--speaker-detection/--no-speaker-detection",
            help="Override speaker detection (Resolve 21+). Defaults to the project setting.",
        ),
    ] = None,
) -> None:
    """Transcribe audio for a bin (recursively) or a single clip."""
    target = _ai_target(ctx, bin=bin, clip=clip)
    target.transcribe(use_speaker_detection=speaker_detection)
    output.emit(
        {"transcribed": target.name, "speaker_detection": speaker_detection},
        fmt=ctx.obj["format"],
    )


@app.command("classify-audio")
def classify_audio_cmd(
    ctx: typer.Context,
    bin: Annotated[str | None, typer.Argument(help="Bin to classify. Defaults to root.")] = None,
    clip: Annotated[
        str | None, typer.Option("--clip", help="Classify a single clip by name instead.")
    ] = None,
    clear: Annotated[
        bool, typer.Option("--clear", help="Clear classification instead of computing it.")
    ] = False,
) -> None:
    """Analyze and classify clip audio into categories (Resolve 21+, Studio)."""
    target = _ai_target(ctx, bin=bin, clip=clip)
    if clear:
        target.clear_audio_classification()
        output.emit({"cleared_classification": target.name}, fmt=ctx.obj["format"])
        return
    target.classify_audio()
    output.emit({"classified": target.name}, fmt=ctx.obj["format"])


@app.command("deblur")
def deblur_cmd(
    ctx: typer.Context,
    bin: Annotated[str | None, typer.Argument(help="Bin to deblur. Defaults to root.")] = None,
    clip: Annotated[
        str | None, typer.Option("--clip", help="Deblur a single clip by name instead.")
    ] = None,
    fmt: Annotated[
        str | None, typer.Option("--format", help="Output container, e.g. mov, mp4.")
    ] = None,
    codec: Annotated[
        str | None, typer.Option("--codec", help="Output codec, e.g. H264, ProRes422.")
    ] = None,
    extreme: Annotated[bool, typer.Option("--extreme", help="Use extreme deblur mode.")] = False,
) -> None:
    """Apply AI motion deblur, rendering new clips (Resolve 21+, Studio)."""
    options: dict[str, object] = {}
    if fmt:
        options["Format"] = fmt
    if codec:
        options["Codec"] = codec
    if extreme:
        options["UseExtremeMode"] = True
    target = _ai_target(ctx, bin=bin, clip=clip)
    result = target.remove_motion_blur(options or None)
    if isinstance(result, list):
        rows = [{"original": orig.name, "deblurred": new.name} for orig, new in result]
        output.emit(rows, fmt=ctx.obj["format"], headline=f"deblurred {len(rows)}")
    else:
        name = result.name if result is not None else None
        output.emit({"deblurred": name}, fmt=ctx.obj["format"])


@app.command("analyze")
def analyze_cmd(
    ctx: typer.Context,
    kind: Annotated[str, typer.Argument(help="Analysis type: intellisearch | slate.")],
    bin: Annotated[str | None, typer.Argument(help="Bin to analyze. Defaults to root.")] = None,
    clip: Annotated[
        str | None, typer.Option("--clip", help="Analyze a single clip by name instead.")
    ] = None,
    faces: Annotated[
        bool, typer.Option("--faces", help="Identify faces (intellisearch only).")
    ] = False,
    better: Annotated[
        bool, typer.Option("--better", help="Use Better mode (intellisearch only).")
    ] = False,
    color: Annotated[
        str, typer.Option("--color", help="Marker color for slate analysis.")
    ] = "Blue",
) -> None:
    """Run Intellisearch or AI Slate ID analysis (Resolve 21+, Studio)."""
    target = _ai_target(ctx, bin=bin, clip=clip)
    if kind == "intellisearch":
        ok = target.analyze_for_intellisearch(identify_faces=faces, better_mode=better)
    elif kind == "slate":
        ok = target.analyze_for_slate(color)
    else:
        raise typer.BadParameter("kind must be 'intellisearch' or 'slate'.")
    output.emit(
        {"analyzed": target.name, "kind": kind, "ok": ok},
        fmt=ctx.obj["format"],
    )
