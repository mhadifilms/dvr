"""The :class:`Resolve` entry point.

`Resolve()` is the front door for everything: connection, app-level
operations, and access to the project / timeline / render namespaces.

Construction is cheap and lazy where possible. The connection itself is
established eagerly because nothing else can work without it; subsequent
domain accessors (``r.project``, ``r.timeline``, ``r.render``) are lazy
properties that re-read state from Resolve on each call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from . import errors
from ._wrap import require
from .connection import connect

if TYPE_CHECKING:
    from .media import MediaStorage
    from .project import ProjectNamespace
    from .render import RenderNamespace
    from .timeline import TimelineNamespace

logger = logging.getLogger("dvr.resolve")


# Resolve's GetCurrentPage / OpenPage strings.
PAGES = ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver")


def _open_page(raw: Any, name: str) -> None:
    if name not in PAGES:
        raise errors.DvrError(
            f"Unknown page: {name!r}",
            cause=f"Page must be one of {PAGES}",
            fix=f"resolve.page = '{PAGES[2]}'",
            state={"requested": name, "valid": list(PAGES)},
        )
    ok = raw.OpenPage(name)
    if ok:
        return
    # On headless / render-farm Resolve instances OpenPage returns None
    # even though scripting otherwise works. Treat that as a no-op iff a
    # project is loaded — the page change is cosmetic for almost every
    # API and renders happen regardless of which page the UI shows.
    pm = raw.GetProjectManager()
    if pm is not None and pm.GetCurrentProject() is not None:
        logger.debug(
            "OpenPage(%r) returned %r on a headless Resolve — continuing",
            name,
            ok,
        )
        return
    raise errors.DvrError(
        f"Could not open page {name!r}",
        cause="Resolve refused the page change (no project loaded?).",
        fix="Load or create a project first.",
        state={"requested": name, "current": raw.GetCurrentPage()},
    )


class PageController:
    """A string-like wrapper around the current page with ``.use()`` context manager.

    Backwards-compatible accessor — ``r.page == "edit"`` works,
    ``str(r.page) == "edit"`` works, ``r.page = "color"`` works (via the
    parent :class:`Resolve.page` setter), and ``with r.page.use("color"):
    ...`` switches and restores.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def name(self) -> str:
        return self._raw.GetCurrentPage()

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"PageController({self.name!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PageController):
            return self.name == other.name
        return self.name == other

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(self.name)

    @contextmanager
    def use(self, page: str) -> Iterator[str]:
        """Switch to ``page`` for the duration of the ``with`` block."""
        previous = self.name
        _open_page(self._raw, page)
        try:
            yield page
        finally:
            if previous and previous != page:
                try:
                    _open_page(self._raw, previous)
                except errors.DvrError as exc:
                    logger.warning("could not restore previous page %r: %s", previous, exc)


class App:
    """App-level operations: pages, layouts, version, quit."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def page(self) -> PageController:
        """The currently visible page (``edit``, ``color``, ``deliver`` ...)."""
        return PageController(self._raw)

    @page.setter
    def page(self, name: str) -> None:
        _open_page(self._raw, name)

    @property
    def version(self) -> str:
        """Resolve's version string, e.g. ``20.3.1``."""
        return self._raw.GetVersionString()

    @property
    def product(self) -> str:
        """``DaVinci Resolve`` or ``DaVinci Resolve Studio``."""
        return self._raw.GetProduct()

    def quit(self) -> None:
        """Quit DaVinci Resolve gracefully."""
        self._raw.Quit()

    def inspect(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "product": self.product,
            "page": str(self.page),
        }


class Resolve:
    """Top-level connection to a running DaVinci Resolve instance.

    Args:
        auto_launch: If True, launch Resolve when it isn't running.
        timeout:     Total seconds to wait for a connection.

    The constructor establishes the connection and validates that Resolve
    is responsive. Use the lazy properties below to navigate further.

    Example:
        >>> from dvr import Resolve
        >>> r = Resolve()
        >>> r.app.page = "deliver"
        >>> r.app.version
        '20.3.1'
    """

    def __init__(self, *, auto_launch: bool = True, timeout: float = 30.0) -> None:
        self._raw = connect(auto_launch=auto_launch, timeout=timeout)
        self._project_manager = require(
            self._raw.GetProjectManager(),
            error=errors.ConnectionError,
            message="Could not get the Resolve ProjectManager.",
            cause="GetProjectManager() returned None — Resolve is reachable but not ready.",
            fix="Wait a moment after launching Resolve, then retry.",
        )

    # --- domain accessors -------------------------------------------------

    @property
    def app(self) -> App:
        """App-level operations (page, layout, version)."""
        return App(self._raw)

    @property
    def page(self) -> PageController:
        """Current Resolve page — shortcut for ``r.app.page``.

        Reads as a string-like value: ``str(r.page)`` returns ``"edit"``,
        ``"color"``, etc. Assignable: ``r.page = "deliver"``. Also exposes
        a context manager via ``r.page.use(...)``.
        """
        return PageController(self._raw)

    @page.setter
    def page(self, name: str) -> None:
        _open_page(self._raw, name)

    @property
    def project_manager(self) -> Any:
        """Raw Resolve ``ProjectManager`` handle.

        For most operations prefer :attr:`project` (the wrapped
        :class:`dvr.project.ProjectNamespace`). Use this when you need to
        reach API methods we don't yet wrap.
        """
        return self._project_manager

    @property
    def pm(self) -> Any:
        """Short alias for :attr:`project_manager`."""
        return self._project_manager

    @property
    def project(self) -> ProjectNamespace:
        """Project-level namespace (current, list, ensure, load, ...)."""
        from .project import ProjectNamespace

        return ProjectNamespace(self._raw, self._project_manager)

    @property
    def timeline(self) -> TimelineNamespace:
        """Timeline-level namespace (current, list, ensure, ...)."""
        from .timeline import TimelineNamespace

        return TimelineNamespace(self)

    @property
    def render(self) -> RenderNamespace:
        """Render queue namespace (submit, watch, status, presets, ...)."""
        from .render import RenderNamespace

        return RenderNamespace(self)

    @property
    def storage(self) -> MediaStorage:
        """Filesystem-side media access (volumes, file listings, bulk import)."""
        from .media import MediaPool, MediaStorage

        current = self.project.current
        if current is None:
            raise errors.ProjectError(
                "No project is currently loaded.",
                fix="Load or create a project first.",
            )
        pool_raw = current.raw.GetMediaPool()
        return MediaStorage(self._raw.GetMediaStorage(), MediaPool(pool_raw, current.raw))

    # --- top-level inspect ------------------------------------------------

    def inspect(self) -> dict[str, Any]:
        """One-call snapshot of app + current project + current timeline."""
        current_project = self.project.current
        current_timeline = current_project.timeline.current if current_project else None
        return {
            "app": self.app.inspect(),
            "current_project": current_project.name if current_project else None,
            "current_timeline": current_timeline.name if current_timeline else None,
            "project_count": len(self.project.list()),
        }

    # --- context manager --------------------------------------------------

    def close(self, *, cancel_pending_renders: bool = True) -> None:
        """Tear down anything ``Resolve()`` may have left running.

        Args:
            cancel_pending_renders: If True (default), stop any in-progress
                                    render and clear any jobs we queued via
                                    :class:`dvr.render.RenderNamespace.submit`
                                    that didn't reach a terminal state.
        """
        if not cancel_pending_renders:
            return
        try:
            current_project = self.project.current
        except errors.DvrError:
            return
        if current_project is None:
            return
        project_raw = current_project.raw
        try:
            if project_raw.IsRenderingInProgress():
                project_raw.StopRendering()
        except Exception:  # boundary — best-effort cleanup
            pass

    def __enter__(self) -> Resolve:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # --- escape hatch -----------------------------------------------------

    @property
    def raw(self) -> Any:
        """The underlying ``scriptapp('Resolve')`` handle.

        Use this only when ``dvr`` does not (yet) expose what you need.
        Anything reached through ``raw`` is unwrapped and unmonitored.
        """
        return self._raw


__all__ = ["App", "PageController", "Resolve"]
