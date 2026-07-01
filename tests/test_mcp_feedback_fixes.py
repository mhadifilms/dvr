"""Regression tests for MCP/spec feedback from real agent workflows."""

from __future__ import annotations

from typing import Any

import pytest

from dvr import errors, lint, spec


def test_parse_spec_rejects_project_mapping() -> None:
    with pytest.raises(errors.SpecError) as ctx:
        spec.parse_spec({"project": {"name": "Bad"}, "timelines": []})

    assert "non-empty string" in ctx.value.message
    assert ctx.value.state["project"] == {"name": "Bad"}


def test_parse_spec_accepts_clip_property_operations() -> None:
    parsed = spec.parse_spec(
        {
            "project": "P",
            "timelines": [
                {
                    "name": "Edit",
                    "clips": [
                        {
                            "selector": {"track_type": "video", "track_index": 2},
                            "properties": {"crop_top": 12, "blend": "multiply"},
                        }
                    ],
                }
            ],
        }
    )
    op = parsed.timelines[0].clip_properties[0]
    assert op.selector == {"track_type": "video", "track_index": 2}
    assert op.properties == {"CropTop": 12.0, "CompositeMode": 4}


def test_spec_apply_sets_clip_properties_idempotently() -> None:
    calls: list[tuple[str, object]] = []

    class _Item:
        name = "shot"
        track_type = "video"
        track_index = 2
        start = 0
        end = 24
        duration = 24

        def __init__(self) -> None:
            self.values = {"CropTop": 0.0, "CompositeMode": 4}

        def get_property(self, key: str) -> object:
            return self.values.get(key)

        def set_property(self, key: str, value: object) -> None:
            calls.append((key, value))
            self.values[key] = value

    item = _Item()

    class _Timeline:
        name = "Edit"

        def track(self, track_type: str, index: int) -> object:
            assert (track_type, index) == ("video", 2)
            return type("_Track", (), {"items": [item]})()

        def items(self, track_type: str | None = None) -> list[_Item]:
            return [item]

        def set_setting(self, key: str, value: str) -> None:
            raise AssertionError("unexpected setting")

        def markers(self) -> dict[int, dict[str, object]]:
            return {}

    class _TimelineNamespace:
        def ensure(self, name: str) -> _Timeline:
            assert name == "Edit"
            return _Timeline()

    class _Project:
        timeline = _TimelineNamespace()

        def set_setting(self, key: str, value: str) -> None:
            raise AssertionError("unexpected project setting")

    class _ProjectNamespace:
        def list(self) -> list[str]:
            return ["P"]

        def ensure(self, name: str) -> _Project:
            assert name == "P"
            return _Project()

    class _Resolve:
        project = _ProjectNamespace()

    parsed = spec.parse_spec(
        {
            "project": "P",
            "timelines": [
                {
                    "name": "Edit",
                    "clip_properties": [
                        {
                            "selector": {"track_type": "video", "track_index": 2},
                            "properties": {"crop_top": 12, "blend": "multiply"},
                        }
                    ],
                }
            ],
        }
    )

    actions = spec.apply(parsed, _Resolve())  # type: ignore[arg-type]

    assert any("clip-properties" in action.target for action in actions)
    assert calls == [("CropTop", 12.0)]


def test_apply_continue_on_error_applies_remaining_settings() -> None:
    applied: dict[str, str] = {}

    class _Project:
        timeline = type("_TimelineNamespace", (), {"ensure": lambda self, name: None})()

        def set_setting(self, key: str, value: str) -> None:
            if key == "bad":
                raise errors.SettingsError("bad setting", state={"key": key})
            applied[key] = value

    class _ProjectNamespace:
        def list(self) -> list[str]:
            return ["P"]

        def ensure(self, name: str) -> _Project:
            assert name == "P"
            return _Project()

    class _Resolve:
        project = _ProjectNamespace()

    parsed = spec.Spec(project="P", settings={"bad": "x", "good": "y"})

    with pytest.raises(errors.SpecError) as ctx:
        spec.apply(parsed, _Resolve(), continue_on_error=True)  # type: ignore[arg-type]

    assert applied == {"good": "y"}
    failures = ctx.value.state["failures"]
    assert failures[0]["target"] == "project:P/setting:bad"


def test_lint_counts_track_item_count_not_only_clip_count() -> None:
    class _Timeline:
        name = "Edit"
        fps = 24.0

        def inspect(self) -> dict[str, Any]:
            return {"tracks": {"video": [{"item_count": 2}]}}

    class _TimelineNamespace:
        current = _Timeline()

        def list(self) -> list[_Timeline]:
            return [_Timeline()]

    class _Project:
        name = "P"
        timeline = _TimelineNamespace()

        def get_setting(self, key: str | None = None) -> str:
            return "davinciYRGBColorManagedv2" if key == "colorScienceMode" else "24"

    class _Resolve:
        class _ProjectNamespace:
            current = _Project()

        project = _ProjectNamespace()
        render = type(
            "_Render",
            (),
            {"current_format_codec": lambda self: {"format": "mov", "codec": "ProRes"}},
        )()

    report = lint.lint(_Resolve())  # type: ignore[arg-type]
    assert "empty_timeline" not in {issue.code for issue in report.issues}


def test_mcp_registry_exposes_cleanup_and_settings_tools() -> None:
    pytest.importorskip("mcp")

    from dvr.mcp.server import _build_registry

    names = {tool.name for tool in _build_registry()}
    assert {
        "project_delete",
        "project_settings_get",
        "timeline_delete",
        "timeline_rename",
        "timeline_clear",
        "media_bin_delete",
    }.issubset(names)


class _Folder:
    def __init__(self, name: str, *, subfolders: list[_Folder] | None = None) -> None:
        self.name = name
        self.subfolders = subfolders or []
        self.clips: list[Any] = []


class _Media:
    def __init__(self) -> None:
        self.root = _Folder("Root", subfolders=[_Folder("A", subfolders=[_Folder("B")])])

    def _find_folder(self, name: str) -> _Folder:
        for folder in self.walk():
            if folder.name == name:
                return folder
        raise AssertionError(name)

    def walk(self) -> list[_Folder]:
        out: list[_Folder] = []

        def visit(folder: _Folder) -> None:
            out.append(folder)
            for child in folder.subfolders:
                visit(child)

        visit(self.root)
        return out


def test_find_bin_path_accepts_slash_paths() -> None:
    pytest.importorskip("mcp")

    from dvr.mcp.server import _find_bin_path

    media = _Media()
    assert _find_bin_path(media, "A/B").name == "B"
    assert _find_bin_path(media, "B").name == "B"


def test_timeline_append_errors_on_partial_append() -> None:
    pytest.importorskip("mcp")

    from dvr.mcp.server import _Context, _h_timeline_append

    class _Clip:
        raw = object()
        name = "clip.mov"
        file_path = "/clip.mov"

    class _AppendMedia(_Media):
        def __init__(self) -> None:
            super().__init__()
            self.root.clips = [_Clip(), _Clip()]

        def append_to_timeline(self, payload: list[dict[str, Any]]) -> list[Any]:
            assert len(payload) == 2
            return [object()]

    class _Timeline:
        name = "Edit"

        def track_count(self, track_type: str) -> int:
            return 2

    class _TimelineNamespace:
        current = _Timeline()

        def set_current(self, name: str) -> _Timeline:
            return self.current

    class _Project:
        media = _AppendMedia()
        timeline = _TimelineNamespace()

    class _ProjectNamespace:
        current = _Project()

        def require_current(self) -> _Project:
            return self.current

    class _Resolve:
        project = _ProjectNamespace()

    class _Cache:
        def get(self) -> _Resolve:
            return _Resolve()

    with pytest.raises(errors.TimelineError) as ctx:
        _h_timeline_append(
            _Context(_Cache()),  # type: ignore[arg-type]
            {
                "items": [
                    {
                        "name": "clip.mov",
                        "media_type": "video",
                        "track_index": 2,
                        "record_frame": 0,
                    },
                    {
                        "name": "clip.mov",
                        "media_type": "video",
                        "track_index": 2,
                        "record_frame": 10,
                    },
                ]
            },
        )

    assert ctx.value.state == {"requested_count": 2, "appended_count": 1}


def test_timeline_append_requires_record_frame_for_non_default_tracks() -> None:
    pytest.importorskip("mcp")

    from dvr.mcp.server import _Context, _h_timeline_append

    class _Timeline:
        name = "Edit"

    class _TimelineNamespace:
        current = _Timeline()

    class _Project:
        media = _Media()
        timeline = _TimelineNamespace()

    class _ProjectNamespace:
        current = _Project()

        def require_current(self) -> _Project:
            return self.current

    class _Resolve:
        project = _ProjectNamespace()

    class _Cache:
        def get(self) -> _Resolve:
            return _Resolve()

    with pytest.raises(errors.TimelineError) as ctx:
        _h_timeline_append(
            _Context(_Cache()),  # type: ignore[arg-type]
            {"items": [{"name": "clip.mov", "media_type": "video", "track_index": 2}]},
        )

    assert "record_frame" in ctx.value.message
