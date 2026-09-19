"""``dvr lut`` and ``dvr dctl`` — manage files in Resolve's LUT directory.

Resolve loads LUTs and DCTLs from a directory on disk, not from the project,
and the scripting API cannot create or inspect them. These commands work on
that directory and then tell you to run ``dvr render refresh-luts`` so Resolve
picks the new files up.

    dvr lut ls
    dvr lut generate dvr/warm.cube --transform "(r ** 0.8, g ** 0.85, b)"
    dvr dctl write dvr/cool.dctl --file ./cool.dctl
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from ... import errors, luts
from .. import output

app = typer.Typer(name="lut", help="LUT files in Resolve's LUT directory.", no_args_is_help=True)
dctl_app = typer.Typer(
    name="dctl", help="DCTL files in Resolve's LUT directory.", no_args_is_help=True
)

_REFRESH_NOTE = "Run `dvr render refresh-luts` to make it selectable in Resolve."


@app.command("root")
def root_cmd(ctx: typer.Context) -> None:
    """Print the LUT directory dvr reads and writes."""
    output.emit({"lut_root": str(luts.lut_root())}, fmt=ctx.obj["format"])


@app.command("ls")
def ls_cmd(
    ctx: typer.Context,
    subdir: Annotated[str | None, typer.Argument(help="Subdirectory to list.")] = None,
) -> None:
    """List LUT files under the LUT directory."""
    rows = luts.list_luts(subdir)
    output.emit(rows, fmt=ctx.obj["format"], headline=f"{len(rows)} LUT(s)")


@app.command("generate")
def generate_cmd(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Destination .cube path inside the LUT directory.")],
    transform: Annotated[
        str,
        typer.Option(
            "--transform",
            help="Python expression over r, g, b in [0,1] returning an (r, g, b) tuple.",
        ),
    ],
    size: Annotated[int, typer.Option("--size", help="Cube size per axis: 17, 33, 65.")] = 33,
    title: Annotated[str | None, typer.Option("--title")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Generate a .cube 3D LUT by evaluating a transform per lattice point.

    The transform runs with imports and dunder access blocked, so it cannot
    reach the filesystem or network.
    """
    written = luts.generate_cube(path, transform, size=size, title=title, overwrite=overwrite)
    output.emit({**written, "note": _REFRESH_NOTE}, fmt=ctx.obj["format"])


@app.command("rm")
def rm_cmd(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="LUT path inside the LUT directory.")],
) -> None:
    """Delete a LUT file from the LUT directory."""
    output.emit(luts.delete_file(path), fmt=ctx.obj["format"])


@dctl_app.command("ls")
def dctl_ls_cmd(
    ctx: typer.Context,
    subdir: Annotated[str | None, typer.Argument(help="Subdirectory to list.")] = None,
) -> None:
    """List DCTL files under the LUT directory."""
    rows = luts.list_dctls(subdir)
    output.emit(rows, fmt=ctx.obj["format"], headline=f"{len(rows)} DCTL(s)")


@dctl_app.command("cat")
def dctl_cat_cmd(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="DCTL path inside the LUT directory.")],
) -> None:
    """Print the source of a DCTL file."""
    typer.echo(luts.read_dctl(path))


@dctl_app.command("write")
def dctl_write_cmd(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="Destination .dctl path inside the LUT directory.")],
    file: Annotated[
        str | None, typer.Option("--file", help="Read source from this file, or - for stdin.")
    ] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Validate and write a DCTL file.

    The source is checked for Resolve's `transform` entry point and balanced
    brackets before anything is written. This is a structural check, not a
    compile: `dvr` runs outside Resolve and cannot invoke its GPU toolchain.
    """
    if file is None:
        raise errors.DvrError(
            "No DCTL source given.",
            fix="Pass --file <path>, or --file - to read from stdin.",
        )
    content = sys.stdin.read() if file == "-" else Path(file).expanduser().read_text("utf-8")
    written = luts.write_dctl(path, content, overwrite=overwrite)
    output.emit({**written, "note": _REFRESH_NOTE}, fmt=ctx.obj["format"])


@dctl_app.command("validate")
def dctl_validate_cmd(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="Local DCTL file to check, or - for stdin.")],
) -> None:
    """Check DCTL source without writing it."""
    content = sys.stdin.read() if file == "-" else Path(file).expanduser().read_text("utf-8")
    luts.validate_dctl(content)
    output.emit({"valid": True, "bytes": len(content)}, fmt=ctx.obj["format"])


@dctl_app.command("rm")
def dctl_rm_cmd(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="DCTL path inside the LUT directory.")],
) -> None:
    """Delete a DCTL file from the LUT directory."""
    output.emit(luts.delete_file(path), fmt=ctx.obj["format"])
