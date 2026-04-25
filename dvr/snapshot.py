"""Project state snapshots — capture, restore, list, delete.

The Resolve UI's undo stack is per-action and clears when the project
closes. ``dvr snapshot save`` captures a structured snapshot of the
project (settings + timelines + markers) to disk; ``dvr snapshot
restore`` re-applies it. Snapshots are JSON; they survive across
sessions and machines.

This isn't a perfect time-machine — Resolve doesn't expose enough state
to round-trip every edit (Fusion node graphs, color grades, magic mask
strokes are all opaque). What it captures:

* project name + selected color/HDR settings
* every timeline's name, FPS, settings, and markers

That's enough for almost every "I broke something, get me back to before"
use case, and it's exactly what ``dvr apply`` knows how to reconcile.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import errors

if TYPE_CHECKING:
    from .resolve import Resolve


SNAPSHOT_VERSION = 1


@dataclass
class Snapshot:
    """A single point-in-time snapshot of a project."""

    name: str
    project: str
    captured_at: str
    version: int = SNAPSHOT_VERSION
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project": self.project,
            "captured_at": self.captured_at,
            "version": self.version,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Snapshot:
        return cls(
            name=str(payload["name"]),
            project=str(payload["project"]),
            captured_at=str(payload["captured_at"]),
            version=int(payload.get("version", SNAPSHOT_VERSION)),
            data=dict(payload.get("data", {})),
        )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def snapshot_dir() -> Path:
    """Return the directory where snapshots live."""
    base = os.environ.get("DVR_SNAPSHOT_DIR")
    target = Path(base).expanduser() if base else Path.home() / ".cache" / "dvr" / "snapshots"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _path_for(name: str) -> Path:
    safe = name.replace("/", "_").replace("\\", "_")
    return snapshot_dir() / f"{safe}.json"


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

# Subset of project settings worth capturing. (The full GetSetting() set
# returns >200 keys; most are UI prefs we don't want to round-trip.)
_CAPTURED_SETTINGS = (
    "colorScienceMode",
    "isAutoColorManage",
    "separateColorSpaceAndGamma",
    "colorSpaceInput",
    "colorSpaceInputGamma",
    "colorSpaceTimeline",
    "colorSpaceTimelineGamma",
    "colorSpaceOutput",
    "colorSpaceOutputGamma",
    "timelineWorkingLuminanceMode",
    "hdrMasteringLuminanceMax",
    "hdrMasteringOn",
    "inputDRT",
    "outputDRT",
    "timelineFrameRate",
    "timelineResolutionWidth",
    "timelineResolutionHeight",
    "videoMonitorFormat",
)


def capture(resolve: Resolve, *, name: str | None = None) -> Snapshot:
    """Capture the currently loaded project's state to a :class:`Snapshot`."""
    project = resolve.project.current
    if project is None:
        raise errors.ProjectError(
            "No project is currently loaded.",
            fix="Load a project before capturing a snapshot.",
        )

    settings: dict[str, str] = {}
    for key in _CAPTURED_SETTINGS:
        try:
            value = project.get_setting(key)
        except Exception:
            continue
        if value is not None:
            settings[key] = str(value)

    timelines: list[dict[str, Any]] = []
    for tl in project.timeline.list():
        markers: list[dict[str, Any]] = []
        for frame, info in (tl.markers() or {}).items():
            markers.append(
                {
                    "frame": int(frame),
                    "color": str(info.get("color", "")),
                    "name": str(info.get("name", "")),
                    "note": str(info.get("note", "")),
                    "duration": int(info.get("duration", 1)),
                    "custom_data": str(info.get("customData", "")),
                }
            )
        timelines.append(
            {
                "name": tl.name,
                "fps": tl.fps,
                "duration_frames": tl.duration_frames,
                "start_timecode": tl.start_timecode,
                "markers": markers,
            }
        )

    captured_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    final_name = name or f"{project.name}@{captured_at.replace(':', '-')}"
    return Snapshot(
        name=final_name,
        project=project.name,
        captured_at=captured_at,
        data={"settings": settings, "timelines": timelines},
    )


def save(snapshot: Snapshot) -> Path:
    """Persist a snapshot to disk."""
    path = _path_for(snapshot.name)
    path.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
    return path


def load(name: str) -> Snapshot:
    """Load a snapshot by name."""
    path = _path_for(name)
    if not path.exists():
        raise errors.DvrError(
            f"Snapshot {name!r} not found.",
            fix=f"Available snapshots: {', '.join(s.name for s in list_snapshots())}",
            state={"path": str(path)},
        )
    return Snapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_snapshots() -> list[Snapshot]:
    """List all snapshots on disk, newest first."""
    out: list[Snapshot] = []
    for f in snapshot_dir().glob("*.json"):
        try:
            out.append(Snapshot.from_dict(json.loads(f.read_text(encoding="utf-8"))))
        except (ValueError, KeyError):
            continue
    out.sort(key=lambda s: s.captured_at, reverse=True)
    return out


def delete(name: str) -> None:
    """Delete a snapshot."""
    path = _path_for(name)
    if not path.exists():
        raise errors.DvrError(f"Snapshot {name!r} not found.")
    path.unlink()


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(resolve: Resolve, snapshot: Snapshot, *, dry_run: bool = False) -> dict[str, int]:
    """Re-apply a snapshot to the live Resolve state.

    Loads / creates the project, applies captured settings, and ensures
    each timeline + its markers exist. Returns counts of what changed.
    """
    project = resolve.project.ensure(snapshot.project)
    counts = {"settings_applied": 0, "timelines_ensured": 0, "markers_added": 0}

    # Settings (HDR-ordered keys first).
    from .spec import SETTINGS_ORDER

    settings = dict(snapshot.data.get("settings", {}))
    for key in SETTINGS_ORDER:
        if key in settings and not dry_run:
            project.set_setting(key, settings[key])
            counts["settings_applied"] += 1
    for key, value in settings.items():
        if key not in SETTINGS_ORDER and not dry_run:
            project.set_setting(key, value)
            counts["settings_applied"] += 1

    # Timelines + markers.
    for tl_data in snapshot.data.get("timelines", []):
        if dry_run:
            continue
        tl = project.timeline.ensure(tl_data["name"])
        counts["timelines_ensured"] += 1
        existing = tl.markers()
        for marker in tl_data.get("markers", []):
            frame = int(marker["frame"])
            if frame in existing:
                continue
            tl.add_marker(
                frame=frame,
                color=str(marker.get("color", "Blue")) or "Blue",
                name=str(marker.get("name", "")),
                note=str(marker.get("note", "")),
                duration=int(marker.get("duration", 1)),
                custom_data=str(marker.get("custom_data", "")),
            )
            counts["markers_added"] += 1

    return counts


__all__ = [
    "Snapshot",
    "capture",
    "delete",
    "list_snapshots",
    "load",
    "restore",
    "save",
    "snapshot_dir",
]
