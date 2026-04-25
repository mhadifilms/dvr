"""Unified interchange import/export.

Resolve supports 20+ interchange formats (AAF, EDL, FCPXML, OTIO, DRT,
ALE, Dolby Vision, HDR10, etc.) but each is gated behind magic enum
constants on the timeline ``Export()`` and media-pool
``ImportTimelineFromFile()`` methods. This module gives them all a
single, format-friendly entry point.

Public API::

    interchange.export(timeline, "out.fcpxml", format="fcpxml-1.10")
    interchange.export(timeline, "out.edl", format="edl-cdl")
    interchange.import_(media_pool, "in.aaf")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import errors

if TYPE_CHECKING:
    from .media import MediaPool
    from .timeline import Timeline

logger = logging.getLogger("dvr.interchange")


# ---------------------------------------------------------------------------
# Format catalog
# ---------------------------------------------------------------------------

# Each entry: (export_type_name, [export_subtype_name | None]).
# We resolve the actual numeric enum at call time off the timeline handle,
# since BMD has changed enum values between versions.

EXPORT_FORMATS: dict[str, tuple[str, str | None]] = {
    "aaf": ("EXPORT_AAF", "EXPORT_AAF_NEW"),
    "aaf-existing": ("EXPORT_AAF", "EXPORT_AAF_EXISTING"),
    "edl": ("EXPORT_EDL", "EXPORT_NONE"),
    "edl-cdl": ("EXPORT_EDL", "EXPORT_CDL"),
    "edl-sdl": ("EXPORT_EDL", "EXPORT_SDL"),
    "edl-missing": ("EXPORT_EDL", "EXPORT_MISSING_CLIPS"),
    "fcp7-xml": ("EXPORT_FCP_7_XML", None),
    "fcpxml-1.8": ("EXPORT_FCPXML_1_8", None),
    "fcpxml-1.9": ("EXPORT_FCPXML_1_9", None),
    "fcpxml-1.10": ("EXPORT_FCPXML_1_10", None),
    "drt": ("EXPORT_DRT", None),
    "otio": ("EXPORT_OTIO", None),
    "csv": ("EXPORT_TEXT_CSV", None),
    "tab": ("EXPORT_TEXT_TAB", None),
    "ale": ("EXPORT_ALE", None),
    "ale-cdl": ("EXPORT_ALE_CDL", None),
    "dolby-vision-2.9": ("EXPORT_DOLBY_VISION_VER_2_9", None),
    "dolby-vision-4.0": ("EXPORT_DOLBY_VISION_VER_4_0", None),
    "dolby-vision-5.1": ("EXPORT_DOLBY_VISION_VER_5_1", None),
    "hdr10-a": ("EXPORT_HDR_10_PROFILE_A", None),
    "hdr10-b": ("EXPORT_HDR_10_PROFILE_B", None),
}


def export_formats() -> list[str]:
    """Return the list of canonical format names accepted by :func:`export`."""
    return sorted(EXPORT_FORMATS.keys())


def _resolve_enum(handle: Any, name: str) -> Any:
    """Resolve an export-enum constant off the timeline/resolve handle."""
    if hasattr(handle, name):
        return getattr(handle, name)
    # Some BMD builds expose enums on the resolve object; fall back to a
    # bare string which the API also accepts in some versions.
    return name


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export(
    timeline: Timeline,
    file_path: str | Path,
    *,
    format: str = "fcpxml-1.10",
) -> str:
    """Export ``timeline`` to ``file_path`` in the given interchange format.

    Args:
        timeline:  A :class:`dvr.Timeline` to export.
        file_path: Destination path. Directory must exist.
        format:    One of the keys in :data:`EXPORT_FORMATS`. See
                   :func:`export_formats` for the live list.

    Returns:
        The absolute string path of the export.
    """
    if format not in EXPORT_FORMATS:
        raise errors.InterchangeError(
            f"Unknown export format {format!r}.",
            cause="The format is not in dvr's catalog.",
            fix=f"Use one of: {', '.join(export_formats())}",
            state={"requested": format},
        )

    type_name, subtype_name = EXPORT_FORMATS[format]
    raw = timeline.raw
    export_type = _resolve_enum(raw, type_name)
    export_subtype = _resolve_enum(raw, subtype_name) if subtype_name else None

    target = str(Path(file_path).expanduser().resolve())
    ok = (
        raw.Export(target, export_type, export_subtype)
        if export_subtype is not None
        else raw.Export(target, export_type)
    )
    if not ok:
        raise errors.InterchangeError(
            f"Failed to export timeline to {target!r}.",
            cause="Timeline.Export returned False.",
            fix="Verify the destination directory exists and is writable.",
            state={"format": format, "file_path": target},
        )
    return target


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_(
    pool: MediaPool,
    file_path: str | Path,
    *,
    options: dict[str, Any] | None = None,
) -> Timeline:
    """Import an interchange file (AAF/EDL/FCPXML/etc.) as a new timeline.

    Resolve auto-detects the format from the file extension and contents.
    """
    return pool.import_timeline(str(file_path), options=options)


__all__ = ["EXPORT_FORMATS", "export", "export_formats", "import_"]
