"""Internal helpers shared by every domain wrapper.

The Resolve API is famously inconsistent: methods can return ``None`` for
"not found", "wrong page", "no current project", or genuine errors — with
no way to distinguish. The helpers here let domain wrappers collapse those
cases into a structured ``DvrError`` with a useful diagnosis.

Nothing here is part of the public API.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from . import errors

T = TypeVar("T")


def require(
    value: T | None,
    *,
    error: type[errors.DvrError] = errors.DvrError,
    message: str,
    cause: str | None = None,
    fix: str | None = None,
    state: dict[str, Any] | None = None,
) -> T:
    """Assert ``value is not None`` or raise a structured error.

    Use this around any raw API call that can return ``None`` to signal
    failure. ``cause``/``fix``/``state`` flow into the resulting exception.
    """
    if value is None:
        raise error(message, cause=cause, fix=fix, state=state)
    return value


def requires_method(
    raw: Any,
    method_name: str,
    *,
    feature: str,
    min_version: str = "21",
    error: type[errors.DvrError] = errors.DvrError,
    state: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """Return ``raw.<method_name>`` or raise if this Resolve lacks it.

    Scripting calls added in newer Resolve releases (notably the
    Resolve 21 Studio/AI surface) don't exist on older builds — the raw
    bridge either lacks the attribute or exposes a non-callable. Rather
    than letting an ``AttributeError`` leak out, collapse the missing
    method into a structured "requires Resolve N" error so callers on
    older versions get an actionable message.
    """
    method = getattr(raw, method_name, None)
    if not callable(method):
        raise error(
            f"{feature} requires DaVinci Resolve {min_version} or newer.",
            cause=f"This DaVinci Resolve build does not expose {method_name}().",
            fix=(
                f"Update to DaVinci Resolve {min_version} Studio (and install any "
                "required Extras via the Extras Download Manager) to use this feature."
            ),
            state=state,
        )
    return cast("Callable[..., Any]", method)


def safe_call(
    fn: Callable[[], T],
    *,
    error: type[errors.DvrError] = errors.DvrError,
    message: str,
    cause: str | None = None,
    fix: str | None = None,
    state: dict[str, Any] | None = None,
) -> T:
    """Run a raw API call and translate exceptions into a ``DvrError``.

    The Resolve API occasionally raises bare ``RuntimeError`` from C++.
    Catch broadly and re-surface with diagnostic context.
    """
    try:
        result = fn()
    except errors.DvrError:
        raise
    except Exception as exc:
        raise error(
            message,
            cause=cause or f"underlying API raised {type(exc).__name__}: {exc}",
            fix=fix,
            state=state,
        ) from exc
    return require(result, error=error, message=message, cause=cause, fix=fix, state=state)


__all__ = ["require", "requires_method", "safe_call"]
