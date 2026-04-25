"""``dvr eval`` / ``dvr exec`` / ``dvr repl`` — scripting escape hatches.

These commands let you run arbitrary Python with a connected ``Resolve``
instance pre-bound, without writing the boilerplate yourself.
"""

from __future__ import annotations

import code
import contextlib
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from ... import errors
from ...resolve import Resolve
from .. import output


def register(app: typer.Typer) -> None:
    @app.command("eval")
    def eval_cmd(
        ctx: typer.Context,
        expression: Annotated[
            str,
            typer.Argument(help="Python expression. `r` is the live Resolve instance."),
        ],
    ) -> None:
        """Evaluate a Python expression with `r = Resolve()` already bound.

        Examples:

            dvr eval "r.app.version"
            dvr eval "r.timeline.current.duration_frames"
            dvr eval "[t.name for t in r.timeline.list()]"
        """
        cfg = ctx.obj or {}
        r = _resolve(cfg)
        ns = _ns(r)
        try:
            value = eval(expression, ns)
        except errors.DvrError as exc:
            output.emit_error(exc, fmt=cfg.get("format"))
            raise typer.Exit(1) from exc
        output.emit(_to_jsonable(value), fmt=cfg.get("format"))

    @app.command("exec")
    def exec_cmd(
        ctx: typer.Context,
        file: Annotated[str, typer.Argument(help="Python file to execute.")],
    ) -> None:
        """Run a Python file with `r = Resolve()` already bound.

        Inside the script, the names ``r`` (Resolve), ``project``, ``timeline``
        (current project / timeline if any), and the ``dvr`` module are
        available without imports.
        """
        cfg = ctx.obj or {}
        path = Path(file).expanduser().resolve()
        if not path.exists():
            typer.echo(f"file not found: {path}", err=True)
            raise typer.Exit(1)
        source = path.read_text(encoding="utf-8")
        r = _resolve(cfg)
        ns = _ns(r)
        ns["__file__"] = str(path)
        ns["__name__"] = "__main__"
        try:
            exec(compile(source, str(path), "exec"), ns)
        except errors.DvrError as exc:
            output.emit_error(exc, fmt=cfg.get("format"))
            raise typer.Exit(1) from exc

    @app.command("repl")
    def repl_cmd(ctx: typer.Context) -> None:
        """Open an interactive Python REPL with `r` bound to a live Resolve."""
        cfg = ctx.obj or {}
        r = _resolve(cfg)
        ns = _ns(r)
        banner = (
            f"dvr repl — Resolve {r.app.version} ({r.app.product})\n"
            "Available: r, project, timeline, dvr\n"
            "Press Ctrl-D to exit."
        )
        with contextlib.suppress(ImportError):
            import readline  # noqa: F401 — enables history if available
        code.interact(banner=banner, local=ns, exitmsg="bye")


def _resolve(cfg: dict[str, Any]) -> Resolve:
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


def _ns(r: Resolve) -> dict[str, Any]:
    import dvr

    project = r.project.current
    timeline = project.timeline.current if project else None
    return {
        "r": r,
        "project": project,
        "timeline": timeline,
        "dvr": dvr,
        "sys": sys,
    }


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "inspect") and callable(value.inspect):
        return value.inspect()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value
