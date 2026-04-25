"""Tests for the timeline primitives added in dvr 0.3.0.

Track.delete, Timeline.delete_track, Timeline.delete_clips,
Timeline.create_compound_from_clips, Clip.is_compound, Clip.source_range.

All tests use fake handles in the same style as test_clip_where.py — no
running Resolve required.
"""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.timeline import Clip, Timeline, Track

# ---------------------------------------------------------------------------
# Fake Resolve handles
# ---------------------------------------------------------------------------


class _FakeTimelineRaw:
    """Minimal fake of Resolve's Timeline scripting handle."""

    def __init__(self) -> None:
        # ``DeleteTrack(type, index) -> bool``
        self.delete_track_calls: list[tuple[str, int]] = []
        self.delete_track_result: bool = True

        # ``DeleteClips([items], ripple) -> bool``
        self.delete_clips_calls: list[tuple[list, bool]] = []
        self.delete_clips_result: bool = True

        # ``CreateCompoundClip([items], {info}) -> TimelineItem``
        self.create_compound_calls: list[tuple[list, dict]] = []
        self.create_compound_result: object | None = object()

    # The methods Track / Timeline call directly:
    def DeleteTrack(self, track_type: str, index: int) -> bool:
        self.delete_track_calls.append((track_type, index))
        return self.delete_track_result

    def DeleteClips(self, items: list, ripple: bool) -> bool:
        self.delete_clips_calls.append((list(items), ripple))
        return self.delete_clips_result

    def CreateCompoundClip(self, items: list, info: dict) -> object | None:
        self.create_compound_calls.append((list(items), dict(info)))
        return self.create_compound_result


class _FakeClipRaw:
    """Minimal fake of Resolve's TimelineItem (Clip) scripting handle."""

    def __init__(
        self,
        *,
        source_start: int = 100,
        source_end: int = 200,
        media_pool_item: object | None = object(),
        type_property: str = "Video",
    ) -> None:
        self._source_start = source_start
        self._source_end = source_end
        self._mpi = media_pool_item
        self._type = type_property

    def GetSourceStartFrame(self) -> int:
        return self._source_start

    def GetSourceEndFrame(self) -> int:
        return self._source_end

    def GetMediaPoolItem(self) -> object | None:
        return self._mpi

    def GetProperty(self, key: str | None = None) -> object:
        if key == "Type":
            return self._type
        return {"Type": self._type}


def _make_timeline(raw: _FakeTimelineRaw) -> Timeline:
    """Build a real Timeline wrapping a fake handle."""
    return Timeline(raw, project=None)


def _make_clip(raw: _FakeClipRaw, *, track_type: str = "video", track_index: int = 1) -> Clip:
    return Clip(raw, track_type=track_type, track_index=track_index)


# ---------------------------------------------------------------------------
# Track.delete
# ---------------------------------------------------------------------------


def test_track_delete_calls_underlying_api() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    track = Track(tl, "audio", 3)
    track.delete()
    assert raw.delete_track_calls == [("audio", 3)]


def test_track_delete_raises_on_failure() -> None:
    raw = _FakeTimelineRaw()
    raw.delete_track_result = False
    tl = _make_timeline(raw)
    track = Track(tl, "video", 2)
    with pytest.raises(errors.TrackError) as info:
        track.delete()
    assert "video" in str(info.value)
    assert "2" in str(info.value)


# ---------------------------------------------------------------------------
# Timeline.delete_track
# ---------------------------------------------------------------------------


def test_timeline_delete_track_validates_type() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    with pytest.raises(errors.TrackError):
        tl.delete_track("photons", 1)
    assert raw.delete_track_calls == []


def test_timeline_delete_track_dispatches() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    tl.delete_track("audio", 5)
    assert raw.delete_track_calls == [("audio", 5)]


def test_timeline_delete_track_raises_with_state() -> None:
    raw = _FakeTimelineRaw()
    raw.delete_track_result = False
    tl = _make_timeline(raw)
    with pytest.raises(errors.TrackError) as info:
        tl.delete_track("audio", 5)
    assert info.value.state == {"track_type": "audio", "index": 5}


# ---------------------------------------------------------------------------
# Timeline.delete_clips
# ---------------------------------------------------------------------------


def test_delete_clips_empty_iterable_is_noop() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    tl.delete_clips([])
    assert raw.delete_clips_calls == []


def test_delete_clips_passes_raw_handles() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    clip_a = _make_clip(_FakeClipRaw())
    clip_b = _make_clip(_FakeClipRaw())
    tl.delete_clips([clip_a, clip_b])
    assert len(raw.delete_clips_calls) == 1
    items, ripple = raw.delete_clips_calls[0]
    assert items == [clip_a.raw, clip_b.raw]
    assert ripple is False


def test_delete_clips_ripple_flag() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    clip = _make_clip(_FakeClipRaw())
    tl.delete_clips([clip], ripple=True)
    _, ripple = raw.delete_clips_calls[0]
    assert ripple is True


def test_delete_clips_raises_on_failure() -> None:
    raw = _FakeTimelineRaw()
    raw.delete_clips_result = False
    tl = _make_timeline(raw)
    clip = _make_clip(_FakeClipRaw())
    with pytest.raises(errors.TimelineError):
        tl.delete_clips([clip])


# ---------------------------------------------------------------------------
# Timeline.create_compound_from_clips
# ---------------------------------------------------------------------------


def test_create_compound_requires_at_least_one_clip() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    with pytest.raises(errors.TimelineError):
        tl.create_compound_from_clips([], name="C1")
    assert raw.create_compound_calls == []


def test_create_compound_dispatches_with_info() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    clip_a = _make_clip(_FakeClipRaw(), track_type="video", track_index=2)
    clip_b = _make_clip(_FakeClipRaw(), track_type="video", track_index=2)
    compound = tl.create_compound_from_clips(
        [clip_a, clip_b],
        name="Group_001",
        start_timecode="01:00:00:00",
    )
    assert isinstance(compound, Clip)
    assert compound.track_type == "video"
    assert compound.track_index == 2
    items, info = raw.create_compound_calls[0]
    assert items == [clip_a.raw, clip_b.raw]
    assert info == {"name": "Group_001", "startTimecode": "01:00:00:00"}


def test_create_compound_omits_optional_start_tc() -> None:
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    clip = _make_clip(_FakeClipRaw())
    tl.create_compound_from_clips([clip], name="Solo")
    _, info = raw.create_compound_calls[0]
    assert "startTimecode" not in info
    assert info == {"name": "Solo"}


def test_create_compound_raises_when_resolve_returns_none() -> None:
    raw = _FakeTimelineRaw()
    raw.create_compound_result = None
    tl = _make_timeline(raw)
    clip = _make_clip(_FakeClipRaw())
    with pytest.raises(errors.TimelineError) as info:
        tl.create_compound_from_clips([clip], name="X")
    assert "X" in str(info.value)


def test_create_compound_accepts_one_shot_iterator() -> None:
    """Regression: clips iterable must be materialized once."""
    raw = _FakeTimelineRaw()
    tl = _make_timeline(raw)
    clip = _make_clip(_FakeClipRaw())
    tl.create_compound_from_clips(iter([clip]), name="Once")
    items, _ = raw.create_compound_calls[0]
    assert items == [clip.raw]


# ---------------------------------------------------------------------------
# Clip.is_compound and Clip.source_range
# ---------------------------------------------------------------------------


def test_source_range() -> None:
    clip = _make_clip(_FakeClipRaw(source_start=24, source_end=240))
    assert clip.source_range == (24, 240)


def test_is_compound_true_when_no_mpi_and_type_matches() -> None:
    clip = _make_clip(_FakeClipRaw(media_pool_item=None, type_property="Compound Clip"))
    assert clip.is_compound is True


def test_is_compound_false_when_mpi_present() -> None:
    clip = _make_clip(_FakeClipRaw(media_pool_item=object(), type_property="Compound Clip"))
    assert clip.is_compound is False


def test_is_compound_false_for_regular_clip() -> None:
    clip = _make_clip(_FakeClipRaw(media_pool_item=None, type_property="Video"))
    assert clip.is_compound is False
