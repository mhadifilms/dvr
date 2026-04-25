"""Audio operations: channel mapping, voice isolation, Fairlight presets.

The Fairlight scripting surface is small. Resolve does not expose EQ /
compression / routing programmatically. What this module covers:

* Reading audio channel mapping (which embedded/linked tracks feed which
  timeline audio channel) for clips and assets.
* Voice isolation (Fairlight feature) on timelines.
* Inserting audio at the playhead.
* Applying named Fairlight presets to the current timeline.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, List  # noqa: UP035

from . import errors

if TYPE_CHECKING:
    from .project import Project
    from .timeline import Timeline, TimelineItem


# ---------------------------------------------------------------------------
# Per-clip / per-asset audio mapping
# ---------------------------------------------------------------------------


def get_clip_audio_mapping(clip: TimelineItem) -> dict[str, Any]:
    """Return the JSON-decoded audio mapping for a timeline clip."""
    raw = clip.raw.GetSourceAudioChannelMapping() or "{}"
    try:
        return dict(json.loads(raw))
    except (TypeError, ValueError):
        return {}


def get_asset_audio_mapping(asset: Any) -> dict[str, Any]:
    """Return the JSON-decoded audio mapping for a media-pool asset."""
    raw = asset.raw.GetAudioMapping() if hasattr(asset, "raw") else asset.GetAudioMapping()
    try:
        return dict(json.loads(raw or "{}"))
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Voice isolation (timeline-level Fairlight feature)
# ---------------------------------------------------------------------------


def voice_isolation_state(timeline: Timeline) -> dict[str, Any]:
    """Return ``{"enabled": bool, "amount": int}`` for the timeline."""
    state = timeline.raw.GetVoiceIsolationState() or {}
    return {
        "enabled": bool(state.get("Enabled", False)),
        "amount": int(state.get("Amount", 0)),
    }


def set_voice_isolation(
    timeline: Timeline,
    *,
    enabled: bool,
    amount: int = 50,
) -> None:
    """Toggle voice isolation; ``amount`` is 0-100."""
    if not 0 <= amount <= 100:
        raise errors.DvrError(
            f"Voice isolation amount must be 0-100, got {amount!r}.",
            state={"amount": amount},
        )
    if not timeline.raw.SetVoiceIsolationState({"Enabled": enabled, "Amount": amount}):
        raise errors.DvrError(
            "Could not set voice isolation state.",
            cause="SetVoiceIsolationState returned False.",
            state={"timeline": timeline.name, "enabled": enabled, "amount": amount},
        )


# ---------------------------------------------------------------------------
# Project-level Fairlight presets
# ---------------------------------------------------------------------------


def fairlight_presets(project: Project) -> List[str]:  # noqa: UP006
    return [str(n) for n in (project.raw.GetFairlightPresets() or [])]


def apply_fairlight_preset(project: Project, name: str) -> None:
    """Apply a named Fairlight preset to the current timeline."""
    if not project.raw.ApplyFairlightPresetToCurrentTimeline(name):
        raise errors.DvrError(
            f"Could not apply Fairlight preset {name!r}.",
            cause="ApplyFairlightPresetToCurrentTimeline returned False.",
            fix=f"Available presets: {fairlight_presets(project)}",
            state={"requested": name},
        )


# ---------------------------------------------------------------------------
# Insert audio at playhead (Fairlight page)
# ---------------------------------------------------------------------------


def insert_audio_at_playhead(
    project: Project,
    *,
    file_path: str,
    offset_samples: int = 0,
    duration_samples: int | None = None,
) -> None:
    """Insert audio at the current track's playhead on the Fairlight page."""
    args = [file_path, offset_samples]
    if duration_samples is not None:
        args.append(duration_samples)
    if not project.raw.InsertAudioToCurrentTrackAtPlayhead(*args):
        raise errors.DvrError(
            f"Could not insert audio {file_path!r}.",
            cause="InsertAudioToCurrentTrackAtPlayhead returned False.",
            fix="Switch to the Fairlight page and select an audio track first.",
            state={
                "file_path": file_path,
                "offset_samples": offset_samples,
                "duration_samples": duration_samples,
            },
        )


__all__ = [
    "apply_fairlight_preset",
    "fairlight_presets",
    "get_asset_audio_mapping",
    "get_clip_audio_mapping",
    "insert_audio_at_playhead",
    "set_voice_isolation",
    "voice_isolation_state",
]
