"""``dvr apply`` and ``dvr plan`` — declarative reconciliation."""

from __future__ import annotations

from typing import Annotated

import typer

from ... import spec as spec_mod
from ...resolve import Resolve
from .. import output


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


def _action_rows(actions: list[spec_mod.Action]) -> list[dict[str, str]]:
    return [{"op": a.op, "target": a.target, "detail": a.detail} for a in actions]


def register(app: typer.Typer) -> None:
    """Register ``apply`` and ``plan`` as top-level commands."""

    @app.command("plan")
    def plan_cmd(
        ctx: typer.Context,
        spec_file: Annotated[str, typer.Argument(help="Path to a YAML or JSON spec.")],
    ) -> None:
        """Show the actions `dvr apply` would take, without executing."""
        cfg = ctx.obj or {}
        resolve = _resolve(ctx)
        spec = spec_mod.load_spec(spec_file)
        actions = spec_mod.plan(spec, resolve)
        output.emit(
            _action_rows(actions),
            fmt=cfg.get("format"),
            headline=f"plan: {spec.project}",
        )

    @app.command("apply")
    def apply_cmd(
        ctx: typer.Context,
        spec_file: Annotated[str, typer.Argument(help="Path to a YAML or JSON spec.")],
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", "-n", help="Print the plan without applying."),
        ] = False,
        yes: Annotated[
            bool,
            typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
        ] = False,
    ) -> None:
        """Reconcile a spec against the live DaVinci Resolve state."""
        cfg = ctx.obj or {}
        resolve = _resolve(ctx)
        spec = spec_mod.load_spec(spec_file)

        actions = spec_mod.plan(spec, resolve)
        output.emit(
            _action_rows(actions),
            fmt=cfg.get("format"),
            headline=f"plan: {spec.project}",
        )

        if dry_run:
            return

        if not yes:
            typer.confirm(f"Apply {len(actions)} action(s) to {spec.project!r}?", abort=True)

        applied = spec_mod.apply(spec, resolve, dry_run=False)
        output.emit(
            {"applied": len(applied), "project": spec.project},
            fmt=cfg.get("format"),
        )
