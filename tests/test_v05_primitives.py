"""Tests for the v0.5 primitives:

- Timeline.duplicate, .find_clip, .find_clips, .find_gaps
- Track.find / .find_all
- MarkerCollection.find / .where
- TimelineItem.set_property(raise_on_failure=False)
- Folder.walk / .all_clips / .find_clip(s) / .delete / .rename
- MediaPool.walk / .find_clip(s) / .find_folder / .delete_folders /
  .delete_timelines / .import_to / .create_subclip
- Clip.set_property(raise_on_failure=False)
- RenderNamespace.submit_per_clip / .render_single_clip
- Project.settings typed proxy
"""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.media import Clip, MediaPool
from dvr.project import Project, Settings
from dvr.timeline import (
    Timeline,
    TimelineItem,
    Track,
)
from tests.conftest import MockNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(name: str, start: int = 0, end: int = 24) -> MockNode:
    return MockNode(
        f"Item({name})",
        {
            "GetName": name,
            "GetStart": start,
            "GetEnd": end,
            "GetDuration": end - start,
            "GetClipEnabled": True,
            "GetMediaPoolItem": None,
            "GetProperty": None,
            "SetProperty": True,
        },
    )


def _make_clip(name: str, **kw) -> MockNode:
    return MockNode(
        f"Clip({name})",
        {
            "GetName": name,
            "GetClipProperty": lambda key=None: (
                {"Clip Name": name, "File Path": kw.get("path", "/x/" + name)}
                if key is None
                else {"Clip Name": name, "File Path": kw.get("path", "/x/" + name)}.get(key, "")
            ),
            "SetClipProperty": True,
        },
    )


# ---------------------------------------------------------------------------
# Timeline.duplicate
# ---------------------------------------------------------------------------


def test_timeline_duplicate_with_name():
    new_raw = MockNode("DupTL", {"GetName": "MyTL_2"})
    raw = MockNode("TL", {"GetName": "MyTL", "DuplicateTimeline": new_raw})
    tl = Timeline(raw, project=MockNode("Project"))
    dup = tl.duplicate("MyTL_2")
    assert isinstance(dup, Timeline)
    assert dup.name == "MyTL_2"
    # Verify it called DuplicateTimeline with the name
    assert ("DuplicateTimeline", ("MyTL_2",), {}) in raw.calls


def test_timeline_duplicate_no_name():
    new_raw = MockNode("DupTL", {"GetName": "MyTL 1"})
    raw = MockNode("TL", {"GetName": "MyTL", "DuplicateTimeline": new_raw})
    tl = Timeline(raw, project=MockNode("Project"))
    dup = tl.duplicate()
    assert dup.name == "MyTL 1"
    assert ("DuplicateTimeline", (), {}) in raw.calls


def test_timeline_duplicate_failure():
    raw = MockNode("TL", {"GetName": "MyTL", "DuplicateTimeline": None})
    tl = Timeline(raw, project=MockNode("Project"))
    with pytest.raises(errors.TimelineError):
        tl.duplicate("X")


# ---------------------------------------------------------------------------
# Track.find / find_all
# ---------------------------------------------------------------------------


def test_track_find_by_name():
    items = [_make_item("a"), _make_item("b"), _make_item("c")]
    tl_raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetTrackCount": 1,
            "GetItemListInTrack": lambda kind, idx: items,
        },
    )
    tl = Timeline(tl_raw, project=MockNode("P"))
    tr = Track(tl, "video", 1)
    found = tr.find(name="b")
    assert found is not None
    assert found.name == "b"
    assert tr.find(name="missing") is None


def test_track_find_predicate():
    items = [_make_item("short", 0, 10), _make_item("long", 0, 100)]
    tl_raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetTrackCount": 1,
            "GetItemListInTrack": lambda kind, idx: items,
        },
    )
    tl = Timeline(tl_raw, project=MockNode("P"))
    tr = Track(tl, "video", 1)
    found = tr.find(predicate=lambda it: it.duration > 50)
    assert found is not None
    assert found.name == "long"


def test_track_find_requires_one_arg():
    tl_raw = MockNode(
        "TL", {"GetName": "TL", "GetTrackCount": 1, "GetItemListInTrack": lambda *a: []}
    )
    tl = Timeline(tl_raw, project=MockNode("P"))
    tr = Track(tl, "video", 1)
    with pytest.raises(errors.TrackError):
        tr.find()
    with pytest.raises(errors.TrackError):
        tr.find(name="x", predicate=lambda it: True)


def test_track_find_all():
    items = [_make_item("a"), _make_item("b"), _make_item("a")]
    tl_raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetTrackCount": 1,
            "GetItemListInTrack": lambda kind, idx: items,
        },
    )
    tl = Timeline(tl_raw, project=MockNode("P"))
    tr = Track(tl, "video", 1)
    matches = tr.find_all(name="a")
    assert len(matches) == 2


# ---------------------------------------------------------------------------
# Timeline.find_clip / find_clips / find_gaps
# ---------------------------------------------------------------------------


def test_timeline_find_clip_across_tracks():
    v1_items = [_make_item("v1clip")]
    a1_items = [_make_item("a1clip")]
    raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetTrackCount": lambda kind: {"video": 1, "audio": 1, "subtitle": 0}.get(kind, 0),
            "GetItemListInTrack": lambda kind, idx: (
                v1_items if kind == "video" else a1_items if kind == "audio" else []
            ),
        },
    )
    tl = Timeline(raw, project=MockNode("P"))
    found = tl.find_clip(name="a1clip")
    assert found is not None
    assert found.name == "a1clip"
    found_v = tl.find_clip(name="v1clip", track_type="video")
    assert found_v is not None
    assert tl.find_clip(name="a1clip", track_type="video") is None


def test_timeline_find_gaps():
    items = [_make_item("a", 24, 48), _make_item("b", 100, 120)]
    raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetStartFrame": 0,
            "GetEndFrame": 200,
            "GetTrackCount": lambda kind: 1 if kind == "video" else 0,
            "GetItemListInTrack": lambda kind, idx: items if kind == "video" else [],
        },
    )
    tl = Timeline(raw, project=MockNode("P"))
    gaps = tl.find_gaps()
    assert gaps == [(0, 24), (48, 100), (120, 200)]


def test_timeline_find_gaps_no_items():
    raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetStartFrame": 0,
            "GetEndFrame": 100,
            "GetTrackCount": lambda kind: 1 if kind == "video" else 0,
            "GetItemListInTrack": lambda kind, idx: [],
        },
    )
    tl = Timeline(raw, project=MockNode("P"))
    assert tl.find_gaps() == [(0, 100)]


# ---------------------------------------------------------------------------
# MarkerCollection.find / where
# ---------------------------------------------------------------------------


def test_markers_find_by_color():
    raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetMarkers": {
                10: {"color": "Red", "name": "x", "customData": "shot1"},
                20: {"color": "Blue", "name": "y", "customData": ""},
                30: {"color": "Red", "name": "z", "customData": "shot2"},
            },
        },
    )
    tl = Timeline(raw, project=MockNode("P"))
    reds = tl.markers.find(color="Red")
    assert [f for f, _ in reds] == [10, 30]


def test_markers_find_by_custom_data():
    raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetMarkers": {
                10: {"color": "Red", "customData": "shot1"},
                20: {"color": "Red", "customData": "shot1"},
            },
        },
    )
    tl = Timeline(raw, project=MockNode("P"))
    matches = tl.markers.find(custom_data="shot1")
    assert len(matches) == 2


def test_markers_where_predicate():
    raw = MockNode(
        "TL",
        {
            "GetName": "TL",
            "GetMarkers": {
                10: {"color": "Red", "duration": 1},
                20: {"color": "Blue", "duration": 24},
            },
        },
    )
    tl = Timeline(raw, project=MockNode("P"))
    long_ones = tl.markers.where(lambda f, m: m.get("duration", 0) > 10)
    assert len(long_ones) == 1
    assert long_ones[0][0] == 20


# ---------------------------------------------------------------------------
# set_property non-raising variants
# ---------------------------------------------------------------------------


def test_timelineitem_set_property_returns_bool():
    raw_ok = MockNode("Item", {"GetName": "ok", "SetProperty": True})
    raw_bad = MockNode("Item", {"GetName": "bad", "SetProperty": False})
    item_ok = TimelineItem(raw_ok, track_type="video", track_index=1)
    item_bad = TimelineItem(raw_bad, track_type="video", track_index=1)
    assert item_ok.set_property("k", "v") is True
    assert item_bad.set_property("k", "v", raise_on_failure=False) is False
    with pytest.raises(errors.ClipError):
        item_bad.set_property("k", "v")


def test_clip_set_property_returns_bool():
    raw_ok = MockNode("Clip", {"GetName": "ok", "SetClipProperty": True, "GetClipProperty": ""})
    raw_bad = MockNode("Clip", {"GetName": "bad", "SetClipProperty": False, "GetClipProperty": ""})
    assert Clip(raw_ok).set_property("k", "v") is True
    assert Clip(raw_bad).set_property("k", "v", raise_on_failure=False) is False
    with pytest.raises(errors.MediaError):
        Clip(raw_bad).set_property("k", "v")


# ---------------------------------------------------------------------------
# Folder.walk / all_clips / find_clip(s) / delete / rename
# ---------------------------------------------------------------------------


def _make_folder_tree():
    """Returns (pool, root_folder_raw) with structure:
    root
      ├─ a (clip1, clip2)
      └─ b
          └─ c (clip3)
    """
    clip1 = _make_clip("clip1")
    clip2 = _make_clip("clip2")
    clip3 = _make_clip("clip3")

    folder_c = MockNode(
        "FolderC",
        {
            "GetName": "c",
            "GetClipList": [clip3],
            "GetSubFolderList": [],
            "SetClipProperty": True,
        },
    )
    folder_b = MockNode(
        "FolderB",
        {
            "GetName": "b",
            "GetClipList": [],
            "GetSubFolderList": [folder_c],
            "SetClipProperty": True,
        },
    )
    folder_a = MockNode(
        "FolderA",
        {
            "GetName": "a",
            "GetClipList": [clip1, clip2],
            "GetSubFolderList": [],
            "SetClipProperty": True,
        },
    )
    root = MockNode(
        "Root",
        {
            "GetName": "Master",
            "GetClipList": [],
            "GetSubFolderList": [folder_a, folder_b],
            "SetClipProperty": True,
        },
    )

    pool_raw = MockNode(
        "MP",
        {
            "GetRootFolder": root,
            "GetCurrentFolder": root,
            "DeleteFolders": True,
            "DeleteTimelines": True,
        },
    )
    project_raw = MockNode("Proj", {"GetMediaStorage": MockNode("Storage")})
    pool = MediaPool(pool_raw, project_raw)
    return pool, root, folder_a, folder_b, folder_c


def test_folder_walk_recursive():
    pool, *_ = _make_folder_tree()
    root_folder = pool.root
    names = [f.name for f in root_folder.walk()]
    assert names == ["Master", "b", "c", "a"] or names == ["Master", "a", "b", "c"]
    # all_clips
    all_clip_names = sorted(c.name for c in root_folder.all_clips())
    assert all_clip_names == ["clip1", "clip2", "clip3"]


def test_folder_find_clip_recursive():
    pool, *_ = _make_folder_tree()
    root_folder = pool.root
    found = root_folder.find_clip(name="clip3")
    assert found is not None
    assert found.name == "clip3"
    assert root_folder.find_clip(name="missing") is None


def test_folder_rename_and_delete():
    pool, _root, fa, *_ = _make_folder_tree()
    folder_a = next(f for f in pool.root.subfolders if f.name == "a")
    folder_a.rename("renamed_a")
    assert ("SetClipProperty", ("Clip Name", "renamed_a"), {}) in fa.calls
    folder_a.delete()
    assert any(call[0] == "DeleteFolders" for call in pool.raw.calls)


# ---------------------------------------------------------------------------
# MediaPool: find_clips, find_folder, walk, delete_folders, delete_timelines, import_to
# ---------------------------------------------------------------------------


def test_mediapool_find_clip_recursive():
    pool, *_ = _make_folder_tree()
    found = pool.find_clip(name="clip3")
    assert found is not None and found.name == "clip3"
    assert pool.find_clip(name="x") is None


def test_mediapool_find_clips_predicate():
    pool, *_ = _make_folder_tree()
    matches = pool.find_clips(predicate=lambda c: c.name.startswith("clip"))
    assert {c.name for c in matches} == {"clip1", "clip2", "clip3"}


def test_mediapool_find_folder():
    pool, *_ = _make_folder_tree()
    f = pool.find_folder("c")
    assert f is not None and f.name == "c"
    assert pool.find_folder("nope") is None


def test_mediapool_delete_folders():
    pool, *_ = _make_folder_tree()
    folder_a = next(f for f in pool.root.subfolders if f.name == "a")
    pool.delete_folders([folder_a])
    assert any(call[0] == "DeleteFolders" for call in pool.raw.calls)


def test_mediapool_delete_timelines_by_name():
    """delete_timelines by name resolves via the project handle."""
    tl1 = MockNode("TL1", {"GetName": "Round_1"})
    tl2 = MockNode("TL2", {"GetName": "Round_2"})
    project_raw = MockNode(
        "Proj",
        {
            "GetTimelineCount": 2,
            "GetTimelineByIndex": lambda i: tl1 if i == 1 else tl2,
        },
    )
    pool_raw = MockNode("MP", {"DeleteTimelines": True})
    pool = MediaPool(pool_raw, project_raw)
    pool.delete_timelines("Round_2")
    delete_calls = [c for c in pool_raw.calls if c[0] == "DeleteTimelines"]
    assert len(delete_calls) == 1
    assert delete_calls[0][1][0] == [tl2]


def test_mediapool_import_to_creates_missing_folder():
    new_folder = MockNode("New", {"GetName": "shots", "SetClipProperty": True})
    root = MockNode(
        "Root",
        {
            "GetName": "Master",
            "GetSubFolderList": [],
            "GetClipList": [],
            "AddSubFolder": new_folder,
            "SetClipProperty": True,
        },
    )
    imported_clip = _make_clip("c1")
    pool_raw = MockNode(
        "MP",
        {
            "GetRootFolder": root,
            "GetCurrentFolder": root,
            "AddSubFolder": new_folder,
            "SetCurrentFolder": True,
            "ImportMedia": [imported_clip],
        },
    )
    pool = MediaPool(pool_raw, MockNode("Proj"))
    out = pool.import_to("shots", ["/path/clip1.mov"])
    assert len(out) == 1
    assert out[0].name == "c1"


def test_mediapool_create_subclip():
    new_clip = _make_clip("subclip_1")
    storage = MockNode("Storage", {"AddItemListToMediaPool": [new_clip]})
    pool_raw = MockNode("MP", {})
    project_raw = MockNode("Proj", {"GetMediaStorage": storage})
    pool = MediaPool(pool_raw, project_raw)
    clip = pool.create_subclip("/master.mov", start=100, end=200, name="my_sub")
    assert clip.name == "subclip_1" or clip.name == "my_sub"
    # Verify the dict shape
    item_calls = storage.calls
    assert item_calls
    payload = item_calls[0][1][0]
    assert payload[0]["FilePath"] == "/master.mov"
    assert payload[0]["StartIndex"] == 100
    assert payload[0]["EndIndex"] == 200


# ---------------------------------------------------------------------------
# Project.settings typed proxy
# ---------------------------------------------------------------------------


def test_settings_proxy_get_set():
    raw = MockNode(
        "Proj",
        {
            "GetName": "P",
            "GetSetting": lambda key=None: (
                "1920" if key == "timelineResolutionWidth" else None if key else {}
            ),
            "SetSetting": True,
        },
    )
    proj = Project(raw, MockNode("PM"))
    s = proj.settings
    assert isinstance(s, Settings)
    # Get via mapped attr
    assert s.timeline_resolution_width == "1920"
    # Set via mapped attr
    s.timeline_resolution_width = 3840
    set_calls = [c for c in raw.calls if c[0] == "SetSetting"]
    # At least one call should target the mapped string key.
    assert any(c[1] == ("timelineResolutionWidth", "3840") for c in set_calls)


def test_settings_proxy_unknown_key_passthrough():
    raw = MockNode("Proj", {"GetSetting": lambda key=None: "passthrough", "SetSetting": True})
    proj = Project(raw, MockNode("PM"))
    s = proj.settings
    # Unknown attribute name passes through as the literal key.
    assert s.someUnmappedKey == "passthrough"


def test_settings_contains():
    raw = MockNode("Proj", {})
    proj = Project(raw, MockNode("PM"))
    assert "timeline_resolution_width" in proj.settings
    assert "definitely_not_a_setting" not in proj.settings


# ---------------------------------------------------------------------------
# Render: submit_per_clip wiring (smoke; full integration is in real Resolve)
# ---------------------------------------------------------------------------


def test_submit_per_clip_wires_marks_and_names():
    """Verify SetRenderSettings was called once per item, with MarkIn/MarkOut."""
    from dvr.render import RenderNamespace
    from dvr.resolve import Resolve

    queue: list[dict] = []
    job_counter = {"n": 0}

    def add_render_job(*a, **kw):
        job_counter["n"] += 1
        queue.append({"JobId": f"job{job_counter['n']}", "OutputFilename": ""})
        return f"job{job_counter['n']}"

    project_raw = MockNode(
        "Proj",
        {
            "GetCurrentRenderFormatAndCodec": {"format": "mov", "codec": "ProRes422HQ"},
            "GetRenderJobList": lambda: list(queue),
            "GetRenderFormats": {},
            "GetRenderCodecs": lambda fmt: {},
            "GetRenderPresetList": [],
            "SetRenderSettings": True,
            "AddRenderJob": add_render_job,
            "StartRendering": True,
            "GetCurrentTimeline": MockNode("TL", {"GetName": "TL"}),
        },
    )
    pm = MockNode("PM", {"GetCurrentProject": project_raw, "SaveProject": True})
    raw_resolve = MockNode(
        "Resolve",
        {
            "GetProjectManager": pm,
            "GetProduct": "DaVinci Resolve Studio",
            "GetVersionString": "20.3-mock",
            "OpenPage": True,
        },
    )

    # Build a Resolve via direct construction (skip real connection).
    r = Resolve.__new__(Resolve)
    r._raw = raw_resolve  # type: ignore[attr-defined]
    r._project_manager = pm  # type: ignore[attr-defined]
    r._socket_path = None  # type: ignore[attr-defined]

    ns = RenderNamespace(r)
    items = [
        TimelineItem(_make_item("shot_001", 0, 24), track_type="video", track_index=2),
        TimelineItem(_make_item("shot_002", 24, 48), track_type="video", track_index=2),
    ]
    jobs = ns.submit_per_clip(
        items,
        target_dir="/tmp/out",
        naming_template="{clip_name}_v001",
        format="mov",
        codec="ProRes422HQ",
        start=False,
    )
    assert len(jobs) == 2
    set_calls = [c for c in project_raw.calls if c[0] == "SetRenderSettings"]
    assert len(set_calls) == 2
    payload_a = set_calls[0][1][0]
    assert payload_a["MarkIn"] == 0
    assert payload_a["MarkOut"] == 24
    assert payload_a["CustomName"] == "shot_001_v001"
    assert payload_a["TargetDir"] == "/tmp/out"
    assert payload_a["SelectAllFrames"] is False
