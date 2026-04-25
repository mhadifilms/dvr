"""Diagnostic exception system.

Every error in `dvr` carries three structured fields:

- ``cause``: the most likely reason the operation failed
- ``fix``:   how to recover (often a snippet of code)
- ``state``: a snapshot of relevant state at the time of failure

The Resolve scripting API is notorious for silent ``None`` returns. The
goal of this module is that every wrapped call decodes the failure into a
``DvrError`` whose ``__str__`` reads like a diagnostic, not a Python
traceback. LLM agents can branch on the error type; humans can read the
fix and move on.
"""

from __future__ import annotations

from typing import Any


class DvrError(Exception):
    """Base exception for all `dvr` failures.

    Args:
        message: Short, present-tense description of what failed.
        cause:   The likely underlying reason. Computed by the caller from
                 read-back state where possible.
        fix:     How to recover. A code snippet or short imperative.
        state:   Relevant state snapshot for diagnostics (project name,
                 current page, queue length, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        cause: str | None = None,
        fix: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.fix = fix
        self.state: dict[str, Any] = state or {}

    def __str__(self) -> str:
        parts = [self.message]
        if self.cause:
            parts.append(f"  Cause: {self.cause}")
        if self.fix:
            parts.append(f"  Fix:   {self.fix}")
        if self.state:
            parts.append(f"  State: {self.state}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON output / structured logs / MCP responses."""
        return {
            "type": type(self).__name__,
            "message": self.message,
            "cause": self.cause,
            "fix": self.fix,
            "state": self.state,
        }


class ConnectionError(DvrError):
    """Could not connect to a running DaVinci Resolve instance."""


class NotInstalledError(DvrError):
    """DaVinci Resolve does not appear to be installed on this system."""


class ScriptingDisabledError(DvrError):
    """External scripting is not enabled in Resolve's preferences."""


class ProjectError(DvrError):
    """A project-level operation failed."""


class TimelineError(DvrError):
    """A timeline-level operation failed."""


class TrackError(DvrError):
    """A track-level operation failed (add / delete / lock / etc.)."""


class ClipError(DvrError):
    """A clip-level (TimelineItem or MediaPoolItem) operation failed."""


class MediaError(DvrError):
    """A media import / relink / proxy operation failed."""


class MediaImportError(MediaError):
    """A media import specifically — distinguishable from relink/proxy failures."""


class TimelineNotFoundError(TimelineError):
    """Looked up a timeline by name and it didn't exist in the current project."""


class RenderError(DvrError):
    """A render submission, monitoring, or completion failed."""


class RenderJobError(RenderError):
    """A single render job failed (vs. queue / config errors)."""


class SettingsError(DvrError):
    """Setting an invalid project or timeline setting key/value."""


class ColorError(DvrError):
    """A color-page operation (grade / CDL / LUT) failed."""


class FusionError(DvrError):
    """A Fusion-comp wrap / unwrap / import / export failed."""


class InterchangeError(DvrError):
    """An import/export of an interchange format (EDL/AAF/FCPXML/...) failed."""


class SpecError(DvrError):
    """A declarative spec failed to parse or reconcile."""


__all__ = [
    "ClipError",
    "ColorError",
    "ConnectionError",
    "DvrError",
    "FusionError",
    "InterchangeError",
    "MediaError",
    "MediaImportError",
    "NotInstalledError",
    "ProjectError",
    "RenderError",
    "RenderJobError",
    "ScriptingDisabledError",
    "SettingsError",
    "SpecError",
    "TimelineError",
    "TimelineNotFoundError",
    "TrackError",
]
