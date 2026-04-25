"""CLI entry point.

This module wires the public library to the ``dvr`` shell command. It is
intentionally a thin shell — every command is one library call followed
by an ``output.emit`` call. New domains should add a ``Typer`` sub-app
under ``dvr/cli/commands/`` and register it here.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from .. import __version__, errors
from ..resolve import Resolve
from . import output
from .commands import apply as apply_cmd
from .commands import clip as clip_cmd
from .commands import completion as completion_cmd
from .commands import diff as diff_cmd
from .commands import lint as lint_cmd
from .commands import mcp as mcp_cmd
from .commands import media as media_cmd
from .commands import project as project_cmd
from .commands import render as render_cmd
from .commands import schema as schema_cmd
from .commands import script as script_cmd
from .commands import serve as serve_cmd
from .commands import snapshot as snapshot_cmd
from .commands import timeline as timeline_cmd
from .plugins import load_plugins, plugin_app

app = typer.Typer(
    name="dvr",
    help="The missing CLI for DaVinci Resolve. Declarative, scriptable, LLM-friendly.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    fmt: Annotated[
        str | None,
        typer.Option(
            "--format",
            "-f",
            help="Output format: json | table | yaml. Auto-detects based on TTY.",
        ),
    ] = None,
    no_launch: Annotated[
        bool,
        typer.Option(
            "--no-launch",
            help="Do not auto-launch DaVinci Resolve if it isn't running.",
        ),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Seconds to wait for Resolve to become reachable.",
            min=1.0,
        ),
    ] = 30.0,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Print the dvr version and exit.",
        ),
    ] = None,
) -> None:
    ctx.obj = {"format": fmt, "auto_launch": not no_launch, "timeout": timeout}


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


@app.command("inspect")
def inspect(ctx: typer.Context) -> None:
    """One-call snapshot of Resolve, current project, and current timeline."""
    with _resolve_session(ctx) as r:
        output.emit(r.inspect(), fmt=ctx.obj["format"], headline="dvr inspect")


@app.command("ping")
def ping(ctx: typer.Context) -> None:
    """Verify the connection to Resolve. Prints version on success."""
    with _resolve_session(ctx) as r:
        output.emit(
            {"connected": True, "version": r.app.version, "product": r.app.product},
            fmt=ctx.obj["format"],
        )


@app.command("page")
def page(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Page to switch to (media|cut|edit|fusion|color|fairlight|deliver)."),
    ] = None,
) -> None:
    """Read or set the current Resolve page."""
    with _resolve_session(ctx) as r:
        if name is None:
            output.emit({"page": r.app.page}, fmt=ctx.obj["format"])
            return
        r.app.page = name
        output.emit({"page": r.app.page}, fmt=ctx.obj["format"])


# ---------------------------------------------------------------------------
# Sub-apps
# ---------------------------------------------------------------------------

app.add_typer(project_cmd.app, name="project")
app.add_typer(timeline_cmd.app, name="timeline")
app.add_typer(clip_cmd.app, name="clip")
app.add_typer(media_cmd.app, name="media")
app.add_typer(render_cmd.app, name="render")
app.add_typer(diff_cmd.app, name="diff")
app.add_typer(snapshot_cmd.app, name="snapshot")
app.add_typer(schema_cmd.app, name="schema")
app.add_typer(serve_cmd.app, name="serve")
app.add_typer(mcp_cmd.app, name="mcp")
app.add_typer(completion_cmd.app, name="completion")
app.add_typer(plugin_app, name="plugin")
apply_cmd.register(app)
lint_cmd.register(app)
script_cmd.register(app)

# Auto-discover and attach external plugins (entry points + user manifest).
# Failures are logged, never raised — the CLI still works without them.
load_plugins(app)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


@contextmanager
def _resolve_session(ctx: typer.Context) -> Iterator[Resolve]:
    """Open a Resolve connection; render structured errors and exit on failure."""
    cfg = ctx.obj or {}
    try:
        r = Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))
    except errors.DvrError as exc:
        output.emit_error(exc, fmt=cfg.get("format"))
        raise typer.Exit(1) from exc
    try:
        yield r
    except errors.DvrError as exc:
        output.emit_error(exc, fmt=cfg.get("format"))
        raise typer.Exit(1) from exc


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except KeyboardInterrupt:
        sys.stderr.write("\ncancelled\n")
        sys.exit(130)


if __name__ == "__main__":
    main()
