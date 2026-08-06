"""Coverage for the DaVinci Resolve 21.0.4 API documentation update."""

from __future__ import annotations

from typing import Any

import pytest

from dvr import errors, schema
from dvr.media import SLATE_MARKER_COLORS, Clip, MotionDeblurSettings
from dvr.project import ProjectNamespace, SpeechGenerationSettings
from dvr.render import RenderSettings
from dvr.resolve import PAGES, App
from dvr.timeline import Timeline


class Rec:
    def __init__(self, **responses: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.responses = responses

    def __getattr__(self, name: str) -> Any:
        if name not in self.responses:
            raise AttributeError(name)

        def call(*args: Any) -> Any:
            self.calls.append((name, args))
            value = self.responses[name]
            return value(*args) if callable(value) else value

        return call


def test_photo_page_is_supported_everywhere() -> None:
    from dvr.mcp import server

    assert "photo" in PAGES
    page_spec = next(spec for spec in server.list_tool_specs() if spec.name == "page_set")
    assert "photo" in page_spec.schema["properties"]["name"]["enum"]


def test_app_resolve_21_preset_surfaces() -> None:
    raw = Rec(
        GetLayoutPresetList=["Editorial"],
        GetBurnInPresetList=["Client"],
        DeleteBurnInPreset=True,
        ImportBurnInPreset=True,
        ExportBurnInPreset=True,
        GetUserPreferencesPresetList=["Remote"],
        LoadUserPreferencesPreset=True,
        SaveUserPreferencesPreset=True,
        DeleteUserPreferencesPreset=True,
        ImportUserPreferencesPreset=True,
        ExportUserPreferencesPreset=True,
    )
    app = App(raw)

    assert app.layout_presets() == ["Editorial"]
    assert app.burn_in_presets() == ["Client"]
    app.delete_burn_in_preset("Client")
    app.import_burn_in_preset("/tmp/client.drbip")
    app.export_burn_in_preset("Client", "/tmp/client.drbip")
    assert app.user_preferences_presets() == ["Remote"]
    app.load_user_preferences_preset("Remote")
    app.save_user_preferences_preset("New")
    app.delete_user_preferences_preset("New")
    app.import_user_preferences_preset("/tmp/preset.dat", "Imported")
    app.export_user_preferences_preset("Remote", "/tmp/preset.dat")


def test_project_attributes_are_wrapped() -> None:
    manager = Rec(
        GetProjectAttributesInCurrentFolder={
            "Show": {"notes": "HDR", "liveCollaborationMode": True}
        }
    )
    assert ProjectNamespace(Rec(), manager).attributes() == {
        "Show": {"notes": "HDR", "liveCollaborationMode": True}
    }


def test_timeline_selected_clips_include_real_track_location() -> None:
    item = Rec(GetUniqueId="item-1", GetName="Selected")
    raw = Rec(
        GetName="Timeline",
        GetSelectedClips=[item],
        GetTrackCount=lambda kind: 1 if kind == "audio" else 0,
        GetItemListInTrack=lambda kind, index: [item] if kind == "audio" else [],
    )
    selected = Timeline(raw, Rec()).selected_clips()
    assert [(clip.name, clip.track_type, clip.track_index) for clip in selected] == [
        ("Selected", "audio", 1)
    ]


def test_slate_marker_colors_are_normalized_and_validated() -> None:
    raw = Rec(GetName="Clip", AnalyzeForSlate=True)
    clip = Clip(raw)
    assert clip.analyze_for_slate("blue") is True
    assert ("AnalyzeForSlate", ("Blue",)) in raw.calls
    with pytest.raises(errors.MediaError):
        clip.analyze_for_slate("Orange")


def test_final_ai_and_render_setting_schemas_match_21_0_4_docs() -> None:
    assert {"EncodingProfile", "Encoder", "UseMoreGpuMemory"} <= set(
        MotionDeblurSettings.__annotations__
    )
    assert {"CustomVoiceFile", "Variation", "GenerationID"} <= set(
        SpeechGenerationSettings.__annotations__
    )
    assert {"UseFullExtents", "AddFrameHandles", "DataBurnIn"} <= set(
        RenderSettings.__annotations__
    )
    assert len(SLATE_MARKER_COLORS) == 16
    assert "timelineSampleRate" in schema.PROJECT_SETTINGS


def test_mcp_exposes_final_ai_options() -> None:
    from dvr.mcp import server

    specs = {spec.name: spec for spec in server.list_tool_specs()}
    deblur = specs["media_deblur"].schema["properties"]
    assert {"filename", "encoding_profile", "use_mark_in_out", "encoder"} <= set(deblur)
    speech = specs["project_generate_speech"].schema["properties"]
    assert {"custom_voice_file", "variation", "generation_id"} <= set(speech)
    assert specs["media_analyze"].schema["properties"]["color"]["enum"] == list(SLATE_MARKER_COLORS)
