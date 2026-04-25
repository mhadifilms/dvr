"""``dvr completion`` — shell completion script generation.

Wraps Click's built-in completion machinery (Typer is a Click subclass).
The generated scripts source themselves into bash/zsh/fish.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer

app = typer.Typer(name="completion", help="Generate shell completion scripts (bash | zsh | fish).")


_INSTALL_HINTS = {
    "bash": (
        'Add to ~/.bashrc:\n  eval "$(_DVR_COMPLETE=bash_source dvr)"\n'
        "Or save the output to ~/.local/share/bash-completion/completions/dvr"
    ),
    "zsh": (
        'Add to ~/.zshrc:\n  eval "$(_DVR_COMPLETE=zsh_source dvr)"\n'
        "Or save the output to a file in $fpath named _dvr."
    ),
    "fish": ("Save the output to ~/.config/fish/completions/dvr.fish"),
}


@app.command("show")
def show(
    ctx: typer.Context,
    shell: Annotated[
        str,
        typer.Argument(help="Shell flavor: bash, zsh, or fish."),
    ],
) -> None:
    """Print a completion script for the given shell to stdout."""
    if shell not in ("bash", "zsh", "fish"):
        typer.echo(f"unsupported shell {shell!r} — use bash, zsh, or fish", err=True)
        raise typer.Exit(2)

    # Click discovers shells from the prog name + env var. Spawning the
    # generation in-process is reliable and works across Click versions.
    from click.shell_completion import shell_complete

    from .. import main as cli_main

    # `shell_complete` writes the script to stdout when given the matching env
    # vars; mimic that contract. Typer apps need to be converted to Click commands.
    os.environ["_DVR_COMPLETE"] = f"{shell}_source"
    click_command = typer.main.get_command(cli_main.app)
    rc = shell_complete(click_command, {}, "dvr", "_DVR_COMPLETE", f"{shell}_source")
    if rc != 0:
        typer.echo(f"completion generation failed (rc={rc})", err=True)
        raise typer.Exit(rc)


@app.command("install")
def install(
    ctx: typer.Context,
    shell: Annotated[
        str,
        typer.Argument(help="Shell flavor: bash, zsh, or fish."),
    ],
) -> None:
    """Print install instructions for the given shell."""
    if shell not in _INSTALL_HINTS:
        typer.echo(f"unsupported shell {shell!r} — use bash, zsh, or fish", err=True)
        raise typer.Exit(2)
    typer.echo(_INSTALL_HINTS[shell])
