"""Shared pytest fixtures.

The headline export here is :func:`mock_resolve` — a lightweight stand-in
for a live DaVinci Resolve handle that lets us unit-test wrapper modules
without needing Resolve installed (or any C extension at all).

Design notes
------------

The Resolve scripting API is a thin RPC wrapper around a C++ object
graph. Every method returns either a primitive, a dict, ``None``, or
another opaque proxy object. Mock-wise that's an easy target: a tiny
:class:`MockNode` with attribute-style method dispatch backed by a
configurable response dict, and a tree builder (``MockResolve``) that
wires up Resolve → ProjectManager → Project → Timeline → MediaPool.

Tests that want a particular behavior shape the mock by writing into
``mock.responses`` before calling the wrapped library code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


class MockNode:
    """A scriptable stand-in for any opaque Resolve C++ object.

    Attribute access returns a callable that consults ``responses`` for
    a method-name keyed entry (a value, a callable, or another node).
    Anything not registered returns ``None`` — which mirrors Resolve's
    own "silent failure" behavior and exercises our error-decoding path.
    """

    def __init__(self, name: str = "MockNode", responses: dict[str, Any] | None = None) -> None:
        self.__dict__["_name"] = name
        self.__dict__["_responses"] = dict(responses or {})
        self.__dict__["_calls"]: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    @property
    def calls(self) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        """List of (method, args, kwargs) actually invoked on this node."""
        return self._calls  # type: ignore[no-any-return]

    @property
    def responses(self) -> dict[str, Any]:
        return self._responses  # type: ignore[no-any-return]

    def __getattr__(self, attr: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            self._calls.append((attr, args, kwargs))
            value = self._responses.get(attr)
            if callable(value):
                return value(*args, **kwargs)
            return value

        return call

    def __setattr__(self, attr: str, value: Any) -> None:
        # Internal slots and any attribute that's *also* a real Python
        # attribute on the instance (set via __dict__) skip the response
        # routing — that's how tests get direct, non-callable access to
        # children like ``mock_resolve.project``.
        if attr.startswith("_") or attr in self.__dict__:
            self.__dict__[attr] = value
            return
        self._responses[attr] = value


class MockResolve(MockNode):
    """A pre-wired mock Resolve tree: project_manager → project → timeline."""

    def __init__(self) -> None:
        timeline = MockNode(
            "Timeline",
            {
                "GetName": "MockTimeline",
                "GetSetting": lambda key=None: "24.0" if key == "timelineFrameRate" else None,
                "GetStartFrame": 0,
                "GetEndFrame": 1440,
                "GetStartTimecode": "01:00:00:00",
                "GetTrackCount": lambda kind: 1,
                "GetItemListInTrack": lambda kind, idx: [],
                "GetMarkers": {},
            },
        )
        project = MockNode(
            "Project",
            {
                "GetName": "MockProject",
                "GetSetting": lambda key=None: None if key else {},
                "SetSetting": lambda key, value: True,
                "GetTimelineCount": 1,
                "GetCurrentTimeline": timeline,
                "GetTimelineByIndex": lambda i: timeline if i == 1 else None,
                "GetMediaPool": MockNode("MediaPool"),
                "GetRenderJobList": [],
                "IsRenderingInProgress": False,
                "GetRenderFormats": {},
                "GetRenderCodecs": lambda fmt: {},
                "GetCurrentRenderFormatAndCodec": {"format": "mov", "codec": "ProRes4444XQ"},
                "GetRenderPresetList": [],
            },
        )
        manager = MockNode(
            "ProjectManager",
            {
                "GetCurrentProject": project,
                "GetProjectListInCurrentFolder": ["MockProject"],
                "GetFolderListInCurrentFolder": [],
                "CreateProject": lambda name: project,
                "LoadProject": lambda name: project,
                "DeleteProject": True,
                "SaveProject": True,
                "CloseProject": lambda p: True,
            },
        )
        super().__init__(
            "Resolve",
            {
                "GetVersionString": "20.3.1-mock",
                "GetProduct": "DaVinci Resolve Studio (Mock)",
                "GetProjectManager": manager,
                "GetCurrentPage": "edit",
                "OpenPage": lambda name: True,
                "Quit": None,
            },
        )
        # Expose the children for tests that want to manipulate state.
        # Stored in __dict__ directly so attribute access returns the
        # MockNode (not a callable wrapper from __getattr__).
        self.__dict__["project_manager"] = manager
        self.__dict__["project"] = project
        self.__dict__["timeline"] = timeline


@pytest.fixture
def mock_resolve() -> MockResolve:
    """A pre-wired mock Resolve tree usable in unit tests."""
    return MockResolve()
