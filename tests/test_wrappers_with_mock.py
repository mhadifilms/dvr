"""Unit tests for the wrapper modules using the mock Resolve fixture.

These exercise the boundary between our wrappers and the raw Resolve
API without needing a real Resolve install.
"""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.media import MediaPool
from dvr.project import Project, ProjectNamespace
from dvr.timeline import Timeline


def test_project_inspect_returns_structured_snapshot(mock_resolve) -> None:
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    info = project.inspect()
    assert info["name"] == "MockProject"
    assert info["timeline_count"] == 1
    assert info["current_timeline"] == "MockTimeline"
    assert info["timelines"] == ["MockTimeline"]


def test_project_namespace_list_and_current(mock_resolve) -> None:
    ns = ProjectNamespace(mock_resolve, mock_resolve.project_manager)
    assert ns.list() == ["MockProject"]
    current = ns.current
    assert current is not None
    assert current.name == "MockProject"


def test_project_namespace_ensure_returns_existing(mock_resolve) -> None:
    ns = ProjectNamespace(mock_resolve, mock_resolve.project_manager)
    project = ns.ensure("MockProject")
    assert project.name == "MockProject"


def test_project_set_setting_failure_decodes_error(mock_resolve) -> None:
    mock_resolve.project.responses["SetSetting"] = lambda *args, **kwargs: False
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    with pytest.raises(errors.SettingsError) as ctx:
        project.set_setting("badKey", "badValue")
    err = ctx.value
    assert "badKey" in err.message
    assert err.cause and "SetSetting returned False" in err.cause
    assert err.state.get("key") == "badKey"


def test_project_save_failure_raises(mock_resolve) -> None:
    mock_resolve.project_manager.responses["SaveProject"] = False
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    with pytest.raises(errors.ProjectError) as ctx:
        project.save()
    assert "save project" in ctx.value.message.lower()


def test_timeline_inspect_returns_tracks_and_settings(mock_resolve) -> None:
    timeline = Timeline(mock_resolve.timeline, mock_resolve.project)
    info = timeline.inspect()
    assert info["name"] == "MockTimeline"
    assert info["fps"] == 24.0
    assert info["start_frame"] == 0
    assert info["end_frame"] == 1440
    assert info["duration_frames"] == 1440
    assert "video" in info["tracks"]


def test_timeline_get_setting_returns_fps(mock_resolve) -> None:
    timeline = Timeline(mock_resolve.timeline, mock_resolve.project)
    assert timeline.get_setting("timelineFrameRate") == "24.0"


def test_timeline_add_marker_failure_raises(mock_resolve) -> None:
    mock_resolve.timeline.responses["AddMarker"] = lambda *args, **kwargs: False
    timeline = Timeline(mock_resolve.timeline, mock_resolve.project)
    with pytest.raises(errors.TimelineError):
        timeline.add_marker(0, color="Red", name="x")


def test_media_pool_inspect_uses_root_bin(mock_resolve) -> None:
    root_bin = mock_resolve.project.responses["GetMediaPool"]
    root_bin.responses["GetRootFolder"] = root_bin
    root_bin.responses["GetCurrentFolder"] = root_bin
    root_bin.responses["GetName"] = "MockBin"
    root_bin.responses["GetClipList"] = []
    root_bin.responses["GetSubFolderList"] = []
    root_bin.responses["GetSelectedClips"] = []
    pool = MediaPool(root_bin, mock_resolve.project)
    info = pool.inspect()
    assert info["current_bin"] == "MockBin"
    assert info["selected_count"] == 0
