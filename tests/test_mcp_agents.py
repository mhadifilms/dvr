"""Tests for agent-oriented MCP surface: resources + workflow tools."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("mcp")

from dvr.mcp.server import (
    _Context,
    _h_timeline_assemble,
    list_resource_specs,
    list_tool_specs,
)


def test_new_tools_registered() -> None:
    names = {s.name for s in list_tool_specs()}
    assert {"render_wait", "timeline_assemble", "spec_export"}.issubset(names)


def test_apply_spec_schema_exposes_safety_levers() -> None:
    apply_spec = next(s for s in list_tool_specs() if s.name == "apply_spec")
    props = apply_spec.schema["properties"]
    assert "transactional" in props
    assert "verify" in props


def test_resource_registry_covers_live_state_and_schema() -> None:
    uris = {r.uri for r in list_resource_specs()}
    assert {
        "dvr://inspect",
        "dvr://project/current",
        "dvr://timeline/current",
        "dvr://media/bins",
        "dvr://render/queue",
        "dvr://doctor",
        "dvr://schema/settings",
        "dvr://schema/clip-properties",
    }.issubset(uris)


def test_static_resources_readable_without_resolve() -> None:
    registry = {r.uri: r for r in list_resource_specs()}

    class _EmptyCache:
        _resolve = None
        _error = None

    ctx = _Context(cache=_EmptyCache())  # type: ignore[arg-type]

    settings = registry["dvr://schema/settings"].handler(ctx)
    assert "colorScienceMode" in settings

    doctor = registry["dvr://doctor"].handler(ctx)
    assert "scripting_lib_present" in doctor
    assert doctor["connection_cached"] is False


def test_needs_resolve_flags() -> None:
    registry = {r.uri: r for r in list_resource_specs()}
    assert registry["dvr://doctor"].needs_resolve is False
    assert registry["dvr://schema/settings"].needs_resolve is False
    assert registry["dvr://inspect"].needs_resolve is True


# ---------------------------------------------------------------------------
# timeline_assemble workflow tool
# ---------------------------------------------------------------------------


class _Clip:
    def __init__(self, name: str) -> None:
        self.name = name
        self.raw = object()
        self.file_path = f"/media/{name}"


class _Folder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.subfolders: list[_Folder] = []
        self.clips: list[_Clip] = []


class _Media:
    def __init__(self) -> None:
        self.root = _Folder("Root")
        self.appended: list[dict[str, Any]] = []

    def find_or_import(self, path: str, *, folder: Any = None) -> _Clip:
        clip = _Clip(path.rsplit("/", 1)[-1])
        self.root.clips.append(clip)
        return clip

    def ensure_folder(self, name: str, *, parent: Any) -> _Folder:
        for sub in parent.subfolders:
            if sub.name == name:
                return sub
        new = _Folder(name)
        parent.subfolders.append(new)
        return new

    def walk(self) -> list[_Folder]:
        return [self.root, *self.root.subfolders]

    def append_to_timeline(self, payload: list[dict[str, Any]]) -> list[Any]:
        self.appended.extend(payload)
        return [object() for _ in payload]


class _Timeline:
    name = "Rough Cut"
    duration_frames = 480

    def __init__(self) -> None:
        self.settings: dict[str, str] = {}

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


class _TimelineNS:
    def __init__(self) -> None:
        self.timeline = _Timeline()

    def ensure(self, name: str) -> _Timeline:
        return self.timeline


class _Project:
    def __init__(self) -> None:
        self.media = _Media()
        self.timeline = _TimelineNS()


class _ProjectNS:
    def __init__(self) -> None:
        self.project = _Project()

    def require_current(self) -> _Project:
        return self.project


class _Resolve:
    def __init__(self) -> None:
        self.project = _ProjectNS()


class _Cache:
    def __init__(self) -> None:
        self.resolve = _Resolve()

    def get(self) -> _Resolve:
        return self.resolve


def test_timeline_assemble_imports_and_appends_in_order() -> None:
    cache = _Cache()
    result = _h_timeline_assemble(
        _Context(cache),  # type: ignore[arg-type]
        {
            "timeline": "Rough Cut",
            "fps": 24,
            "items": [
                {"path": "/media/a.mov"},
                {"path": "/media/b.mov", "start_frame": 10, "end_frame": 50},
            ],
        },
    )
    assert result["timeline"] == "Rough Cut"
    assert result["appended"] == 2
    assert result["imported"] == 2

    media = cache.resolve.project.project.media
    assert len(media.appended) == 2
    assert media.appended[1]["startFrame"] == 10
    assert media.appended[1]["endFrame"] == 50

    tl = cache.resolve.project.project.timeline.timeline
    assert tl.settings["timelineFrameRate"] == "24"


def test_tools_and_resources_serialize_to_json() -> None:
    # Guard against schema objects that json.dumps can't handle.
    for spec in list_tool_specs():
        json.dumps(spec.schema)
    for res in list_resource_specs():
        assert res.uri.startswith("dvr://")
