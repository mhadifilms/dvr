"""Discoverable catalogs of valid setting keys, codecs, and clip properties.

The Resolve API has no introspection — there is no way to ask "what
values can I pass to ``SetSetting('colorScienceMode', ...)``?". The only
authoritative answer is "trial and error" or "diff against the doc PDF".

This module assembles a catalog of *known-good* values from three
sources:

1. Static data baked into ``dvr`` (the keys we already use in
   ``dvr.spec.COLOR_PRESETS``, the export-format catalog from
   ``dvr.interchange``).
2. Live values, when a Resolve connection is available — e.g. the
   actual list of formats and codecs the running Resolve build supports.
3. The list of common clip properties, which never changes within a
   major version and is documented in BMD's API PDF.

The CLI command ``dvr schema <topic>`` exposes each catalog as JSON
(or as a table for human consumption).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import interchange, spec

if TYPE_CHECKING:
    from .resolve import Resolve


# Static — known clip properties exposed by TimelineItem.GetProperty/SetProperty.
# Values are (type, valid range / enum). Source: DaVinci_Resolve_API_v20.3.txt.
CLIP_PROPERTIES: dict[str, dict[str, Any]] = {
    "Pan": {"type": "float", "range": [-32768.0, 32767.0]},
    "Tilt": {"type": "float", "range": [-32768.0, 32767.0]},
    "ZoomX": {"type": "float", "range": [0.0, 100.0]},
    "ZoomY": {"type": "float", "range": [0.0, 100.0]},
    "ZoomGang": {"type": "bool"},
    "RotationAngle": {"type": "float", "range": [-360.0, 360.0]},
    "AnchorPointX": {"type": "float"},
    "AnchorPointY": {"type": "float"},
    "Pitch": {"type": "float"},
    "Yaw": {"type": "float"},
    "FlipX": {"type": "bool"},
    "FlipY": {"type": "bool"},
    "CropLeft": {"type": "float", "range": [0.0, 1.0]},
    "CropRight": {"type": "float", "range": [0.0, 1.0]},
    "CropTop": {"type": "float", "range": [0.0, 1.0]},
    "CropBottom": {"type": "float", "range": [0.0, 1.0]},
    "CropSoftness": {"type": "float"},
    "CropRetain": {"type": "bool"},
    "CompositeMode": {
        "type": "enum",
        "values": [
            "Normal",
            "Add",
            "Subtract",
            "Difference",
            "Multiply",
            "Screen",
            "Overlay",
            "HardLight",
            "SoftLight",
            "Darken",
            "Lighten",
            "ColorDodge",
            "ColorBurn",
            "Exclusion",
            "Hue",
            "Saturation",
            "Color",
            "Luminosity",
        ],
    },
    "Opacity": {"type": "float", "range": [0.0, 100.0]},
    "Distortion": {"type": "float", "range": [-1.0, 1.0]},
    "RetimeProcess": {
        "type": "enum",
        "values": ["UseProjectSetting", "NearestFrame", "FrameBlend", "OpticalFlow"],
    },
    "MotionEstimation": {
        "type": "enum",
        "values": [
            "Standard",
            "Enhanced",
            "Faster",
            "Normal",
            "Better",
            "Best",
            "SpeedWarp",
        ],
    },
    "Scaling": {
        "type": "enum",
        "values": [
            "Crop",
            "Fit",
            "Fill",
            "Stretch",
            "Center",
        ],
    },
    "ResizeFilter": {
        "type": "enum",
        "values": [
            "Sharper",
            "Smoother",
            "Bicubic",
            "Bilinear",
            "Bessel",
            "BoxFilter",
            "CatmullRom",
            "Cubic",
            "Gaussian",
            "Lanczos",
            "Mitchell",
            "NearestNeighbor",
            "Quadratic",
            "Sinc",
        ],
    },
}


# Static — frequently-set project settings worth surfacing.
PROJECT_SETTINGS: dict[str, dict[str, Any]] = {
    "colorScienceMode": {
        "type": "enum",
        "values": ["davinciYRGB", "davinciYRGBColorManagedv2", "acescct", "acescc"],
    },
    "colorSpaceInput": {
        "type": "enum-or-string",
        "common": ["Rec.709", "Rec.2020", "P3-D65", "ARRI Wide Gamut", "Sony S-Gamut3"],
    },
    "colorSpaceTimeline": {"type": "enum-or-string", "common": ["Rec.709", "Rec.2020", "P3-D65"]},
    "colorSpaceOutput": {"type": "enum-or-string", "common": ["Rec.709", "Rec.2020", "P3-D65"]},
    "colorSpaceInputGamma": {
        "type": "enum-or-string",
        "common": ["Gamma 2.4", "ST2084", "Linear", "Log3G10", "S-Log3"],
    },
    "colorSpaceTimelineGamma": {
        "type": "enum-or-string",
        "common": ["Gamma 2.4", "Rec.2100 ST2084"],
    },
    "colorSpaceOutputGamma": {"type": "enum-or-string", "common": ["Gamma 2.4", "Rec.2100 ST2084"]},
    "timelineWorkingLuminanceMode": {
        "type": "enum",
        "values": ["SDR 100", "HDR 1000", "HDR 2000", "HDR 4000", "HDR 10000", "Custom"],
    },
    "timelineFrameRate": {
        "type": "string-fps",
        "common": ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"],
    },
    "timelineResolutionWidth": {"type": "int"},
    "timelineResolutionHeight": {"type": "int"},
    "hdrMasteringOn": {"type": "bool-string", "values": ["0", "1"]},
    "isAutoColorManage": {"type": "bool-string", "values": ["0", "1"]},
    "separateColorSpaceAndGamma": {"type": "bool-string", "values": ["0", "1"]},
}


# Static — interchange formats from dvr.interchange.
def export_formats() -> list[str]:
    return interchange.export_formats()


# Color presets baked into dvr.spec.
def color_presets() -> dict[str, dict[str, str]]:
    return dict(spec.COLOR_PRESETS)


# ---------------------------------------------------------------------------
# Live-state catalogs (require a Resolve connection)
# ---------------------------------------------------------------------------


def render_formats(resolve: Resolve) -> dict[str, str]:
    return resolve.render.formats()


def render_codecs(resolve: Resolve, format_name: str) -> dict[str, str]:
    return resolve.render.codecs(format_name)


def render_codec_matrix(resolve: Resolve) -> dict[str, dict[str, str]]:
    """Return ``{format: {codec_id: codec_label}}`` for every supported pair."""
    matrix: dict[str, dict[str, str]] = {}
    for fmt in resolve.render.formats():
        try:
            matrix[fmt] = resolve.render.codecs(fmt)
        except Exception:
            matrix[fmt] = {}
    return matrix


def render_presets(resolve: Resolve) -> list[str]:
    return resolve.render.presets()


# ---------------------------------------------------------------------------
# Topic dispatch (used by the CLI)
# ---------------------------------------------------------------------------


def get_topic(topic: str, resolve: Resolve | None = None) -> Any:
    """Return the catalog for ``topic``. Some topics need a live ``resolve``."""
    if topic == "clip-properties":
        return CLIP_PROPERTIES
    if topic == "settings":
        return PROJECT_SETTINGS
    if topic == "export-formats":
        return export_formats()
    if topic == "color-presets":
        return color_presets()
    if topic == "render-formats":
        if resolve is None:
            return None
        return render_formats(resolve)
    if topic == "render-codecs":
        if resolve is None:
            return None
        return render_codec_matrix(resolve)
    if topic == "render-presets":
        if resolve is None:
            return None
        return render_presets(resolve)
    raise ValueError(
        f"Unknown schema topic {topic!r}. "
        "Available: clip-properties, settings, export-formats, color-presets, "
        "render-formats, render-codecs, render-presets."
    )


TOPICS: tuple[str, ...] = (
    "clip-properties",
    "settings",
    "export-formats",
    "color-presets",
    "render-formats",
    "render-codecs",
    "render-presets",
)


__all__ = [
    "CLIP_PROPERTIES",
    "PROJECT_SETTINGS",
    "TOPICS",
    "color_presets",
    "export_formats",
    "get_topic",
    "render_codec_matrix",
    "render_codecs",
    "render_formats",
    "render_presets",
]
