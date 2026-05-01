"""``MediaPool.delete_timelines`` must accept a raw fusionscript handle.

Fusionscript proxy objects are not classes that satisfy ``isinstance(x, Iterable)`` —
the ABC check raises ``TypeError: issubclass() arg 1 must be a class``. The wrapper
has to treat single-handle inputs as scalars before falling through to the
iterable-unpacking branch.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dvr.media import MediaPool


def _fake_proxy(name: str = "T1"):
    """Stand-in for a fusionscript Timeline handle (no __iter__, has GetName)."""

    class _Proxy:
        def __init__(self, name: str) -> None:
            self._name = name

        def GetName(self) -> str:  # noqa: N802 — fusionscript surface
            return self._name

    p = _Proxy(name)
    # Confirm the proxy fails the ABC check the way fusionscript objects do.
    assert not hasattr(p, "__iter__")
    return p


def test_delete_timelines_accepts_single_raw_handle():
    """A single raw fusionscript handle must not blow up the ABC isinstance check."""
    pool_raw = MagicMock()
    pool_raw.DeleteTimelines.return_value = True
    project_raw = MagicMock()

    pool = MediaPool(pool_raw, project_raw)

    handle = _fake_proxy("MyTimeline")
    pool.delete_timelines(handle)  # must not raise

    pool_raw.DeleteTimelines.assert_called_once()
    args = pool_raw.DeleteTimelines.call_args.args
    assert args[0] == [handle]


def test_delete_timelines_still_accepts_list():
    """Don't regress the iterable-of-handles path."""
    pool_raw = MagicMock()
    pool_raw.DeleteTimelines.return_value = True
    project_raw = MagicMock()

    pool = MediaPool(pool_raw, project_raw)

    handles = [_fake_proxy("A"), _fake_proxy("B")]
    pool.delete_timelines(handles)
    pool_raw.DeleteTimelines.assert_called_once()
    assert pool_raw.DeleteTimelines.call_args.args[0] == handles
