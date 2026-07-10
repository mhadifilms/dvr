"""Coverage for the newly-wrapped DaVinci Resolve API surface.

Each test instantiates a wrapper around a recording fake raw handle and
asserts the wrapper calls the documented Resolve method and surfaces the
result. No live Resolve is required.
"""

from __future__ import annotations

from typing import Any

import pytest

from dvr import errors
from dvr.media import Clip, Folder, MediaPool, MediaStorage
from dvr.project import Project, ProjectNamespace
from dvr.render import RenderNamespace
from dvr.resolve import App
from dvr.timeline import MarkerCollection, Timeline, TimelineItem


class Rec:
    """A recording fake: only declared methods exist (others raise AttributeError)."""

    def __init__(self, **responses: Any) -> None:
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "_responses", responses)

    def __getattr__(self, name: str) -> Any:
        responses = object.__getattribute__(self, "_responses")
        if name not in responses:
            raise AttributeError(name)
        calls = object.__getattribute__(self, "calls")

        def call(*args: Any, **kwargs: Any) -> Any:
            calls.append((name, args, kwargs))
            value = responses[name]
            return value(*args, **kwargs) if callable(value) else value

        return call

    def recorded(self) -> list[str]:
        return [c[0] for c in object.__getattribute__(self, "calls")]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def test_app_layout_presets() -> None:
    raw = Rec(SaveLayoutPreset=True, LoadLayoutPreset=True, DeleteLayoutPreset=True)
    app = App(raw)
    app.save_layout("L")
    app.load_layout("L")
    app.delete_layout("L")
    assert raw.recorded() == ["SaveLayoutPreset", "LoadLayoutPreset", "DeleteLayoutPreset"]


def test_app_layout_failure_raises() -> None:
    app = App(Rec(SaveLayoutPreset=False))
    with pytest.raises(errors.DvrError):
        app.save_layout("L")


def test_app_keyframe_mode_roundtrip() -> None:
    raw = Rec(GetKeyframeMode=2, SetKeyframeMode=True)
    app = App(raw)
    assert app.keyframe_mode == 2
    app.keyframe_mode = 1
    assert ("SetKeyframeMode", (1,), {}) in object.__getattribute__(raw, "calls")


def test_app_fusion_handle() -> None:
    sentinel = object()
    app = App(Rec(Fusion=sentinel))
    assert app.fusion is sentinel


# ---------------------------------------------------------------------------
# ProjectManager (ProjectNamespace)
# ---------------------------------------------------------------------------


def test_pm_db_folder_navigation() -> None:
    mgr = Rec(
        CreateFolder=True,
        OpenFolder=True,
        GotoRootFolder=True,
        GotoParentFolder=True,
        GetCurrentFolder="Shoot1",
        GetFolderListInCurrentFolder=[],
    )
    ns = ProjectNamespace(Rec(), mgr)
    ns.create_folder("Shoot1")
    ns.open_folder("Shoot1")
    ns.goto_root_folder()
    ns.goto_parent_folder()
    assert ns.current_folder() == "Shoot1"


def test_pm_databases() -> None:
    mgr = Rec(
        GetCurrentDatabase={"DbType": "Disk", "DbName": "Local"},
        GetDatabaseList=[{"DbType": "Disk", "DbName": "Local"}],
        SetCurrentDatabase=True,
    )
    ns = ProjectNamespace(Rec(), mgr)
    assert ns.current_database()["DbName"] == "Local"
    assert ns.databases()[0]["DbType"] == "Disk"
    ns.set_current_database({"DbType": "Disk", "DbName": "Local"})


def test_pm_cloud_requires_method() -> None:
    ns = ProjectNamespace(Rec(), Rec())  # manager lacks CreateCloudProject
    with pytest.raises(errors.ProjectError):
        ns.create_cloud_project({"name": "x"})


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def test_project_color_groups() -> None:
    group_raw = Rec(GetName="DPX")
    raw = Rec(
        GetColorGroupsList=[group_raw],
        AddColorGroup=lambda name: Rec(GetName=name),
        DeleteColorGroup=True,
    )
    proj = Project(raw, Rec())
    groups = proj.color_groups()
    assert groups[0].name == "DPX"
    created = proj.add_color_group("New")
    assert created.name == "New"
    proj.delete_color_group(created)


def test_project_export_still_and_unique_id() -> None:
    raw = Rec(ExportCurrentFrameAsStill=True, GetUniqueId="proj-123")
    proj = Project(raw, Rec())
    proj.export_current_frame_as_still("/tmp/x.png")
    assert proj.unique_id == "proj-123"


def test_project_quick_export() -> None:
    raw = Rec(
        GetQuickExportRenderPresets=["H.264 Master"],
        RenderWithQuickExport=lambda s: {"path": s["TargetDir"]},
    )
    proj = Project(raw, Rec())
    assert proj.quick_export_presets() == ["H.264 Master"]
    result = proj.quick_export("/out", "H.264 Master")
    assert result["path"] == "/out"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _render_ns(raw: Rec) -> RenderNamespace:
    class _Cur:
        def __init__(self, r: Rec) -> None:
            self.raw = r

    class _NS:
        def __init__(self, r: Rec) -> None:
            self.current = _Cur(r)

    class _R:
        def __init__(self, r: Rec) -> None:
            self.project = _NS(r)

    return RenderNamespace(_R(raw))


def test_render_mode_get_set() -> None:
    raw = Rec(GetCurrentRenderMode=1, SetCurrentRenderMode=True)
    ns = _render_ns(raw)
    assert ns.render_mode() == "single"
    ns.set_render_mode("individual")
    assert ("SetCurrentRenderMode", (0,), {}) in object.__getattribute__(raw, "calls")


def test_render_resolutions() -> None:
    raw = Rec(GetRenderResolutions=[{"Width": 3840, "Height": 2160}])
    ns = _render_ns(raw)
    assert ns.resolutions("mov", "ProRes")[0]["Width"] == 3840


def test_render_mode_bad_value() -> None:
    ns = _render_ns(Rec(GetCurrentRenderMode=0))
    with pytest.raises(errors.RenderError):
        ns.set_render_mode("sideways")


# ---------------------------------------------------------------------------
# MediaStorage / MediaPool / Folder / Clip
# ---------------------------------------------------------------------------


def test_media_storage_mattes() -> None:
    raw = Rec(AddClipMattesToMediaPool=True, AddTimelineMattesToMediaPool=[Rec(GetName="m")])
    storage = MediaStorage(raw, MediaPool(Rec(), Rec()))
    clip = Clip(Rec(GetName="shot"))
    storage.add_clip_mattes(clip, ["/m.png"])
    out = storage.add_timeline_mattes(["/t.png"])
    assert len(out) == 1


def test_media_pool_move_folders_and_metadata() -> None:
    raw = Rec(MoveFolders=True, ExportMetadata=True, GetUniqueId="mp-1")
    pool = MediaPool(raw, Rec())
    f1 = Folder(Rec(GetName="A"), pool)
    target = Folder(Rec(GetName="T"), pool)
    pool.move_folders([f1], target)
    pool.export_metadata("/x.csv")
    assert pool.unique_id == "mp-1"
    assert raw.recorded()[:2] == ["MoveFolders", "ExportMetadata"]


def test_media_pool_stereo_and_import_bin() -> None:
    raw = Rec(CreateStereoClip=lambda a, b: Rec(GetName="3D"), ImportFolderFromFile=True)
    pool = MediaPool(raw, Rec())
    left = Clip(Rec(GetName="L"))
    right = Clip(Rec(GetName="R"))
    stereo = pool.create_stereo_clip(left, right)
    assert stereo.name == "3D"
    assert pool.import_folder_from_file("/bin.drb") is True


def test_clip_third_party_metadata_and_media_id() -> None:
    raw = Rec(
        GetMediaId="mid-9",
        GetUniqueId="uid-9",
        GetThirdPartyMetadata=lambda k=None: {"x": "y"},
        SetThirdPartyMetadata=True,
    )
    clip = Clip(raw)
    assert clip.media_id == "mid-9"
    assert clip.unique_id == "uid-9"
    assert clip.get_third_party_metadata()["x"] == "y"
    clip.set_third_party_metadata("k", "v")


def test_clip_marker_custom_data() -> None:
    raw = Rec(
        GetMarkerByCustomData=lambda d: {12: {"customData": d}},
        UpdateMarkerCustomData=True,
        DeleteMarkerByCustomData=True,
    )
    clip = Clip(raw)
    assert 12 in clip.get_marker_by_custom_data("k")
    clip.update_marker_custom_data(12, "k2")
    clip.delete_marker_by_custom_data("k2")


# ---------------------------------------------------------------------------
# Timeline / TimelineItem
# ---------------------------------------------------------------------------


def test_timeline_start_timecode_setter() -> None:
    raw = Rec(GetStartTimecode="01:00:00:00", SetStartTimecode=True, GetName="TL")
    tl = Timeline(raw, Rec())
    tl.start_timecode = "02:00:00:00"
    assert ("SetStartTimecode", ("02:00:00:00",), {}) in object.__getattribute__(raw, "calls")


def test_timeline_insert_generator() -> None:
    raw = Rec(
        GetName="TL",
        InsertFusionGeneratorIntoTimeline=lambda name: Rec(GetName=name, GetUniqueId="g1"),
        GetTrackCount=lambda kind: 1,
        GetItemListInTrack=lambda kind, idx: [],
    )
    tl = Timeline(raw, Rec())
    item = tl.insert_generator("Gradient", fusion=True)
    assert item.name == "Gradient"


def test_timeline_grab_stills_and_import_into() -> None:
    raw = Rec(GetName="TL", GrabAllStills=[Rec(), Rec()], ImportIntoTimeline=True)
    tl = Timeline(raw, Rec())
    assert len(tl.grab_all_stills(1)) == 2
    assert tl.import_into("/x.aaf") is True


def test_timeline_set_clips_linked_and_stereo() -> None:
    raw = Rec(GetName="TL", SetClipsLinked=True, ConvertTimelineToStereo=True)
    tl = Timeline(raw, Rec())
    item = TimelineItem(Rec(GetName="c"), track_type="video", track_index=1)
    tl.set_clips_linked([item], False)
    assert tl.convert_to_stereo() is True


def test_timeline_marker_custom_data_collection() -> None:
    raw = Rec(
        GetName="TL",
        GetMarkerByCustomData=lambda d: {5: {"customData": d}},
        UpdateMarkerCustomData=True,
        DeleteMarkerByCustomData=True,
    )
    tl = Timeline(raw, Rec())
    markers = MarkerCollection(tl)
    assert 5 in markers.get_by_custom_data("k")
    markers.update_custom_data(5, "k2")
    markers.remove_by_custom_data("k2")


def test_timeline_item_offsets_and_sidecar() -> None:
    raw = Rec(
        GetName="c",
        GetLeftOffset=12,
        GetRightOffset=24,
        UpdateSidecar=True,
        GetFusionCompCount=3,
        GetUniqueId="ti-1",
    )
    item = TimelineItem(raw, track_type="video", track_index=1)
    assert item.handles == (12, 24)
    assert item.update_sidecar() is True
    assert item.fusion_comp_count == 3
    assert item.unique_id == "ti-1"


def test_timeline_item_clip_color_roundtrip() -> None:
    raw = Rec(
        GetName="c",
        GetClipColor="Blue",
        SetClipColor=True,
        ClearClipColor=True,
    )
    item = TimelineItem(raw, track_type="video", track_index=1)
    assert item.clip_color == "Blue"
    item.clip_color = "Yellow"
    item.clip_color = ""
    assert ("SetClipColor", ("Yellow",), {}) in object.__getattribute__(raw, "calls")
    assert "ClearClipColor" in raw.recorded()


def test_timeline_item_stereo_params() -> None:
    raw = Rec(
        GetName="c",
        GetStereoConvergenceValues={0.0: 1.0},
        GetStereoLeftFloatingWindowParams={0.0: {"left": 0.1}},
    )
    item = TimelineItem(raw, track_type="video", track_index=1)
    assert item.stereo_convergence()[0.0] == 1.0
    assert item.stereo_floating_window("left")[0.0]["left"] == 0.1
    with pytest.raises(errors.ClipError):
        item.stereo_floating_window("middle")


# ---------------------------------------------------------------------------
# MCP registry
# ---------------------------------------------------------------------------


def test_mcp_new_tools_registered() -> None:
    from dvr.mcp import server

    names = {s.name for s in server.list_tool_specs()}
    expected = {
        "timeline_set_start_timecode",
        "timeline_add_generator",
        "timeline_grab_stills",
        "timeline_import_into",
        "render_mode",
        "render_resolutions",
        "render_refresh_luts",
        "project_color_groups",
        "project_export_still",
        "media_export_metadata",
        "media_import_bin",
    }
    assert expected <= names
