"""``dvr lint`` — pre-flight validation."""

from __future__ import annotations

import typer

from ... import lint as lint_mod
from .. import output
from ..session import resolve_from_ctx


def register(app: typer.Typer) -> None:
    @app.command("lint")
    def lint_cmd(ctx: typer.Context) -> None:
        """Run pre-flight checks on the current project / timeline / render config.

        Exit code is 1 if any errors are found, 0 otherwise. Warnings and infos
        do not affect the exit code.
        """
        cfg = ctx.obj or {}
        r = resolve_from_ctx(ctx)
        report = lint_mod.lint(r)
        output.emit(report.to_dict(), fmt=cfg.get("format"), headline="lint")
        if report.errors:
            raise typer.Exit(1)
