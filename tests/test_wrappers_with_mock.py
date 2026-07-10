"""Unit tests for the wrapper modules using the mock Resolve fixture.

These exercise the boundary between our wrappers and the raw Resolve
API without needing a real Resolve install.
"""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.media import Clip, Folder, MediaPool
from dvr.project import Project, ProjectNamespace
from dvr.resolve import App
from dvr.timeline import Timeline

from .conftest import MockNode


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


def test_project_namespace_ensure_returns_current_outside_folder_listing(mock_resolve) -> None:
    mock_resolve.project_manager.responses["GetProjectListInCurrentFolder"] = []

    def unexpected_create(_name):
        raise AssertionError("ensure must not create a duplicate of the active project")

    mock_resolve.project_manager.responses["CreateProject"] = unexpected_create
    ns = ProjectNamespace(mock_resolve, mock_resolve.project_manager)

    assert ns.ensure("MockProject").name == "MockProject"


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


# --- Resolve 21 transcription signature -----------------------------------


def test_clip_transcribe_calls_no_args_by_default() -> None:
    raw = MockNode("Clip", {"TranscribeAudio": True, "GetName": "shot01"})
    Clip(raw).transcribe()
    name, args, _ = raw.calls[-1]
    assert name == "TranscribeAudio"
    # Must NOT pass the legacy language string positionally (Resolve 21
    # reads the first positional as useSpeakerDetection).
    assert args == ()


def test_clip_transcribe_passes_speaker_detection_flag() -> None:
    raw = MockNode("Clip", {"TranscribeAudio": True, "GetName": "shot01"})
    Clip(raw).transcribe(use_speaker_detection=True)
    name, args, _ = raw.calls[-1]
    assert name == "TranscribeAudio"
    assert args == (True,)


def test_clip_transcribe_failure_raises() -> None:
    raw = MockNode("Clip", {"TranscribeAudio": False, "GetName": "shot01"})
    with pytest.raises(errors.MediaError):
        Clip(raw).transcribe()


def test_folder_transcribe_calls_no_args_by_default() -> None:
    raw = MockNode("Folder", {"TranscribeAudio": True, "GetName": "Bin 1"})
    Folder(raw, MockNode("MediaPool")).transcribe()
    name, args, _ = raw.calls[-1]
    assert name == "TranscribeAudio"
    assert args == ()


# --- Resolve 21 AI / Studio additions -------------------------------------


def test_clip_classify_audio_failure_raises() -> None:
    raw = MockNode("Clip", {"PerformAudioClassification": False, "GetName": "shot01"})
    with pytest.raises(errors.MediaError):
        Clip(raw).classify_audio()


def test_clip_remove_motion_blur_returns_new_clip() -> None:
    new_raw = MockNode("Clip", {"GetName": "shot01_deblur"})
    raw = MockNode("Clip", {"RemoveMotionBlur": new_raw, "GetName": "shot01"})
    result = Clip(raw).remove_motion_blur({"UseExtremeMode": True})
    assert isinstance(result, Clip)
    assert result.name == "shot01_deblur"


def test_clip_analyze_for_intellisearch_returns_bool() -> None:
    raw = MockNode("Clip", {"AnalyzeForIntellisearch": True, "GetName": "shot01"})
    assert Clip(raw).analyze_for_intellisearch(identify_faces=True) is True
    _, args, _ = raw.calls[-1]
    assert args == (True, False)


def test_folder_remove_motion_blur_returns_pairs() -> None:
    orig, new = MockNode("Clip", {"GetName": "a"}), MockNode("Clip", {"GetName": "a_db"})
    raw = MockNode("Folder", {"RemoveMotionBlur": [[orig, new]], "GetName": "Bin 1"})
    pairs = Folder(raw, MockNode("MediaPool")).remove_motion_blur()
    assert len(pairs) == 1
    assert pairs[0][0].name == "a"
    assert pairs[0][1].name == "a_db"


def test_project_generate_speech_returns_clip(mock_resolve) -> None:
    new_clip = MockNode("Clip", {"GetName": "vo_take1"})
    mock_resolve.project.responses["GenerateSpeech"] = lambda *a, **k: new_clip
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    clip = project.generate_speech({"TextInput": "hi"}, "01:00:00:00")
    assert isinstance(clip, Clip)
    assert clip.name == "vo_take1"


def test_project_reset_intellisearch_failure_raises(mock_resolve) -> None:
    mock_resolve.project.responses["ResetIntellisearchAnalysis"] = False
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    with pytest.raises(errors.ProjectError):
        project.reset_intellisearch_analysis()


def test_app_disable_background_tasks_is_noop_when_absent(mock_resolve) -> None:
    # Older builds lack the call — must not raise.
    App(mock_resolve).disable_background_tasks()


def test_app_disable_background_tasks_invokes_when_present() -> None:
    raw = MockNode(
        "Resolve",
        {"DisableBackgroundTasksForCurrentResolveSession": None, "GetVersionString": "21.0"},
    )
    App(raw).disable_background_tasks()
    assert any(c[0] == "DisableBackgroundTasksForCurrentResolveSession" for c in raw.calls)


# --- Backward compatibility: pre-21 builds lack the new AI methods --------


class _LegacyClipRaw:
    """A pre-21 MediaPoolItem stub: only the methods that existed in v20."""

    def GetName(self) -> str:
        return "legacy_clip"

    def TranscribeAudio(self, *args: object) -> bool:
        return True

    def ClearTranscription(self) -> bool:
        return True


def test_transcribe_works_on_legacy_build_without_speaker_arg() -> None:
    # Default path must call TranscribeAudio() with no args and succeed.
    Clip(_LegacyClipRaw()).transcribe()


def test_classify_audio_on_legacy_build_raises_version_error() -> None:
    with pytest.raises(errors.MediaError) as ctx:
        Clip(_LegacyClipRaw()).classify_audio()
    assert "21" in ctx.value.message
    assert ctx.value.cause and "PerformAudioClassification" in ctx.value.cause


def test_remove_motion_blur_on_legacy_build_raises_version_error() -> None:
    with pytest.raises(errors.MediaError) as ctx:
        Clip(_LegacyClipRaw()).remove_motion_blur()
    assert "21" in ctx.value.message


def test_analyze_for_slate_on_legacy_build_raises_version_error() -> None:
    with pytest.raises(errors.MediaError):
        Clip(_LegacyClipRaw()).analyze_for_slate("Blue")


class _LegacyProjectRaw:
    """A pre-21 Project stub lacking the AI methods."""

    def GetName(self) -> str:
        return "legacy_project"


def test_project_reset_intellisearch_on_legacy_build_raises() -> None:
    project = Project(_LegacyProjectRaw(), MockNode("ProjectManager"))
    with pytest.raises(errors.ProjectError) as ctx:
        project.reset_intellisearch_analysis()
    assert "21" in ctx.value.message


def test_project_generate_speech_on_legacy_build_raises() -> None:
    project = Project(_LegacyProjectRaw(), MockNode("ProjectManager"))
    with pytest.raises(errors.ProjectError):
        project.generate_speech({"TextInput": "hi"}, "01:00:00:00")
