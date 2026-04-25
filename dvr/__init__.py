"""dvr — the missing CLI and Python library for DaVinci Resolve.

This package exposes a small, stable public API. Internal modules are
prefixed with ``_`` and may change between releases. The two things you
almost always want are:

    from dvr import Resolve, errors

Open a connection with ``r = Resolve()`` and navigate from there.
"""

from __future__ import annotations

from . import audio, diff, errors, gallery, interchange, lint, schema, snapshot, spec
from .color import ColorGroup, ColorOps, NodeGraph
from .media import (
    Asset,  # deprecated alias of Clip (kept for back-compat)
    Bin,  # deprecated alias of Folder
    Clip,
    Folder,
    MediaPool,
    MediaPoolItem,
    MediaStorage,
)
from .project import Project, ProjectNamespace, Settings
from .render import RenderJob, RenderNamespace
from .resolve import App, PageController, Resolve
from .timeline import (
    ClipFusion,  # deprecated alias of ItemFusion
    ClipQuery,  # deprecated alias of ItemQuery
    ItemFusion,
    ItemQuery,
    MarkerCollection,
    Takes,
    Timeline,
    TimelineItem,
    TimelineNamespace,
    Track,
    TrackCollection,
    TrackList,
)

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - generated at build time
    __version__ = "0.0.0+local"

__all__ = [
    "App",
    "Asset",
    "Bin",
    "Clip",
    "ClipFusion",
    "ClipQuery",
    "ColorGroup",
    "ColorOps",
    "Folder",
    "ItemFusion",
    "ItemQuery",
    "MarkerCollection",
    "MediaPool",
    "MediaPoolItem",
    "MediaStorage",
    "NodeGraph",
    "PageController",
    "Project",
    "ProjectNamespace",
    "RenderJob",
    "RenderNamespace",
    "Resolve",
    "Settings",
    "Takes",
    "Timeline",
    "TimelineItem",
    "TimelineNamespace",
    "Track",
    "TrackCollection",
    "TrackList",
    "__version__",
    "audio",
    "diff",
    "errors",
    "gallery",
    "interchange",
    "lint",
    "schema",
    "snapshot",
    "spec",
]
