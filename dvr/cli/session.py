"""Shared CLI helpers for opening a Resolve connection.

Every ``dvr`` sub-command used to carry its own copy of a three-line
``_resolve(ctx)`` factory. They now share this one, and uncaught
:class:`~dvr.errors.DvrError` exceptions are rendered as structured
output by the top-level handler in :mod:`dvr.cli.main` — so commands can
simply let library errors propagate.
"""

from __future__ import annotations

import typer

from ..project import Project
from ..resolve import Resolve


def resolve_from_ctx(ctx: typer.Context) -> Resolve:
    """Open a Resolve connection using the root command's global options."""
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


def current_project(ctx: typer.Context) -> Project:
    """Connect and return the current project, or raise a structured error."""
    return resolve_from_ctx(ctx).project.require_current()


__all__ = ["current_project", "resolve_from_ctx"]
