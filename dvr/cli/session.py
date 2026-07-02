"""Shared CLI helpers for opening a Resolve connection.

Every ``dvr`` sub-command used to carry its own copy of a three-line
``_resolve(ctx)`` factory. They now share this one, and uncaught
:class:`~dvr.errors.DvrError` exceptions are rendered as structured
output by the top-level handler in :mod:`dvr.cli.main` — so commands can
simply let library errors propagate.

When the CLI runs *inside* the dvr daemon (see :mod:`dvr.daemon`), the
daemon installs a resolve provider here so every command reuses the
daemon's persistent connection instead of paying the 2-3s cold
``scriptapp()`` handshake per invocation.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from ..project import Project
from ..resolve import Resolve

# Installed by the daemon (or tests) to reuse a persistent connection.
_resolve_provider: Callable[[], Resolve] | None = None


def set_resolve_provider(provider: Callable[[], Resolve] | None) -> None:
    """Install (or clear) a factory that supplies the Resolve connection.

    When set, :func:`resolve_from_ctx` calls it instead of constructing a
    fresh :class:`Resolve` — this is how the daemon shares one live
    connection across every CLI command it executes.
    """
    global _resolve_provider
    _resolve_provider = provider


def resolve_from_ctx(ctx: typer.Context) -> Resolve:
    """Open a Resolve connection using the root command's global options."""
    if _resolve_provider is not None:
        return _resolve_provider()
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


def current_project(ctx: typer.Context) -> Project:
    """Connect and return the current project, or raise a structured error."""
    return resolve_from_ctx(ctx).project.require_current()


__all__ = ["current_project", "resolve_from_ctx", "set_resolve_provider"]
