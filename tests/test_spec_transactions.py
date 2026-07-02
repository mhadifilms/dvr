"""Tests for spec v2: bins, tracks, verify, transactional rollback, export."""

from __future__ import annotations

from typing import Any

import pytest

from dvr import errors, spec

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeFolder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.subfolders: list[_FakeFolder] = []


class _FakeMedia:
    def __init__(self) -> None:
        self.root = _FakeFolder("Root")
        self.ensured: list[str] = []

    def ensure_folder_path(self, path: str) -> _FakeFolder:
        self.ensured.append(path)
        current = self.root
        for part in [p for p in path.split("/") if p]:
            for sub in current.subfolders:
                if sub.name == part:
                    current = sub
                    break
            else:
                new = _FakeFolder(part)
                current.subfolders.append(new)
                current = new
        return current


class _FakeTimeline:
    def __init__(self, name: str) -> None:
        self.name = name
        self.settings: dict[str, Any] = {}
        self._tracks = {"video": 1, "audio": 1, "subtitle": 0}
        self.fps = 24.0
        self.duration_frames = 0
        self.start_timecode = "01:00:00:00"

    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value

    def get_setting(self, key: str) -> Any:
        return self.settings.get(key)

    def track_count(self, track_type: str) -> int:
        return self._tracks[track_type]

    def add_track(self, track_type: str) -> None:
        self._tracks[track_type] += 1

    def markers(self) -> dict[int, dict[str, Any]]:
        return {}

    def add_marker(self, **kwargs: Any) -> None:
        pass

    def items(self, track_type: str | None = None) -> list[Any]:
        return []


class _FakeTimelineNS:
    def __init__(self) -> None:
        self.timelines: dict[str, _FakeTimeline] = {}

    def ensure(self, name: str) -> _FakeTimeline:
        return self.timelines.setdefault(name, _FakeTimeline(name))

    def list(self) -> list[_FakeTimeline]:
        return list(self.timelines.values())


class _FakeProject:
    def __init__(self, name: str, *, fail_settings: bool = False) -> None:
        self.name = name
        self.media = _FakeMedia()
        self.timeline = _FakeTimelineNS()
        self.settings: dict[str, Any] = {}
        self._fail_settings = fail_settings

    def set_setting(self, key: str, value: Any) -> None:
        if self._fail_settings:
            raise errors.SettingsError(f"rejected {key}")
        self.settings[key] = value

    def get_setting(self, key: str | None = None) -> Any:
        if key is None:
            return dict(self.settings)
        return self.settings.get(key)


class _FakeProjectNS:
    def __init__(self, project: _FakeProject, existing: list[str]) -> None:
        self._project = project
        self._existing = existing

    def list(self) -> list[str]:
        return list(self._existing)

    def ensure(self, name: str) -> _FakeProject:
        return self._project

    def require_current(self) -> _FakeProject:
        return self._project

    @property
    def current(self) -> _FakeProject:
        return self._project


class _FakeResolve:
    def __init__(self, project: _FakeProject, *, existing: list[str] | None = None) -> None:
        self.project = _FakeProjectNS(project, existing or [])


# ---------------------------------------------------------------------------
# Parsing + planning
# ---------------------------------------------------------------------------


def test_parse_spec_bins_and_tracks() -> None:
    parsed = spec.parse_spec(
        {
            "project": "Show",
            "bins": ["Footage/Day01", "Audio"],
            "timelines": [{"name": "Edit", "tracks": {"video": 3, "audio": 4}}],
        }
    )
    assert parsed.bins == ["Footage/Day01", "Audio"]
    assert parsed.timelines[0].tracks == {"video": 3, "audio": 4}


def test_parse_spec_rejects_bad_bins_and_tracks() -> None:
    with pytest.raises(errors.SpecError):
        spec.parse_spec({"project": "S", "bins": [{"name": "x"}]})
    with pytest.raises(errors.SpecError):
        spec.parse_spec({"project": "S", "timelines": [{"name": "E", "tracks": {"vfx": 2}}]})


def test_plan_includes_bins_and_tracks() -> None:
    parsed = spec.parse_spec(
        {
            "project": "Show",
            "bins": ["Footage"],
            "timelines": [{"name": "Edit", "tracks": {"video": 2}}],
        }
    )
    resolve = _FakeResolve(_FakeProject("Show"))
    targets = [a.target for a in spec.plan(parsed, resolve)]  # type: ignore[arg-type]
    assert "bin:Footage" in targets
    assert "timeline:Edit/tracks:video" in targets


# ---------------------------------------------------------------------------
# Apply: bins, tracks, verify
# ---------------------------------------------------------------------------


def test_apply_ensures_bins_and_tracks() -> None:
    parsed = spec.parse_spec(
        {
            "project": "Show",
            "bins": ["Footage/Day01"],
            "timelines": [{"name": "Edit", "tracks": {"video": 3}}],
        }
    )
    project = _FakeProject("Show")
    spec.apply(parsed, _FakeResolve(project), run_hooks=False)  # type: ignore[arg-type]
    assert project.media.ensured == ["Footage/Day01"]
    assert project.timeline.timelines["Edit"].track_count("video") == 3


def test_verified_set_setting_detects_silent_rejection() -> None:
    class _Silent:
        def set_setting(self, key: str, value: Any) -> None:
            pass  # pretends to succeed

        def get_setting(self, key: str) -> Any:
            return "old-value"

    with pytest.raises(errors.SettingsError) as exc:
        spec._verified_set_setting(_Silent(), "timelineFrameRate", "24")
    assert exc.value.state["read_back"] == "old-value"


def test_verified_set_setting_accepts_persisted_value() -> None:
    class _Honest:
        def __init__(self) -> None:
            self.stored: dict[str, Any] = {}

        def set_setting(self, key: str, value: Any) -> None:
            self.stored[key] = value

        def get_setting(self, key: str) -> Any:
            return self.stored.get(key)

    spec._verified_set_setting(_Honest(), "timelineFrameRate", "24")  # no raise


# ---------------------------------------------------------------------------
# Transactional rollback
# ---------------------------------------------------------------------------


def test_transactional_apply_rolls_back_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from dvr import snapshot as snapshot_mod

    restored: list[str] = []
    fake_snap = snapshot_mod.Snapshot(
        name="pre-apply-Show", project="Show", captured_at="now", data={}
    )
    monkeypatch.setattr(snapshot_mod, "capture", lambda resolve, name=None: fake_snap)
    monkeypatch.setattr(snapshot_mod, "save", lambda snap: "/tmp/x.json")
    monkeypatch.setattr(
        snapshot_mod, "restore", lambda resolve, snap, **kw: restored.append(snap.name)
    )

    parsed = spec.parse_spec({"project": "Show", "settings": {"someKey": "v"}})
    project = _FakeProject("Show", fail_settings=True)
    resolve = _FakeResolve(project, existing=["Show"])

    with pytest.raises(errors.SpecError) as exc:
        spec.apply(parsed, resolve, run_hooks=False, transactional=True)  # type: ignore[arg-type]

    assert restored == ["pre-apply-Show"]
    assert "rolled back" in exc.value.message
    assert exc.value.state["snapshot"] == "pre-apply-Show"


def test_transactional_apply_without_existing_project_raises_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = spec.parse_spec({"project": "New", "settings": {"someKey": "v"}})
    project = _FakeProject("New", fail_settings=True)
    resolve = _FakeResolve(project, existing=[])  # project doesn't exist yet

    with pytest.raises(errors.SettingsError):
        spec.apply(parsed, resolve, run_hooks=False, transactional=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Export (from_live)
# ---------------------------------------------------------------------------


def test_from_live_round_trips_through_parse() -> None:
    project = _FakeProject("Show")
    project.settings["timelineFrameRate"] = "24"
    project.media.ensure_folder_path("Footage/Day01")
    tl = project.timeline.ensure("Edit")
    tl._tracks["video"] = 2

    data = spec.from_live(_FakeResolve(project))  # type: ignore[arg-type]
    assert data["project"] == "Show"
    assert data["settings"]["timelineFrameRate"] == "24"
    assert data["bins"] == ["Footage", "Footage/Day01"]
    assert data["timelines"][0]["tracks"]["video"] == 2

    # The exported dict must be a valid spec.
    reparsed = spec.parse_spec(data)
    assert reparsed.project == "Show"
    assert reparsed.timelines[0].tracks["video"] == 2
