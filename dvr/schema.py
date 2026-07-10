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

from . import errors, interchange, spec

if TYPE_CHECKING:
    from .resolve import Resolve


# ---------------------------------------------------------------------------
# Parity matrix (the dvr ↔ pmr contract)
# ---------------------------------------------------------------------------
# status values:
#   both       — implemented in dvr and pmr with the same routing
#   dvr-only   — Resolve API supports it; pmr raises NotSupportedError
#   pmr-only   — Premiere API supports it; dvr raises NotSupportedError
# Keep in sync with the sibling repo's schema.py. CI checks this table
# against the registered CLI commands and MCP tools.

PARITY: dict[str, dict[str, Any]] = {
    "ping": {"status": "both"},
    "inspect": {"status": "both"},
    "version": {"status": "both"},
    "doctor": {"status": "both"},
    "reconnect": {"status": "both"},
    "page.get": {"status": "dvr-only", "reason": "Premiere has no scriptable workspaces"},
    "page.set": {"status": "dvr-only", "reason": "Premiere has no scriptable workspaces"},
    "project.list": {
        "status": "both",
        "note": "pmr lists open projects (file-based, no PM database)",
    },
    "project.current": {"status": "both"},
    "project.ensure": {"status": "both"},
    "project.create": {"status": "both"},
    "project.load": {"status": "both"},
    "project.save": {"status": "both"},
    "project.delete": {"status": "both", "note": "pmr deletes the .prproj file when closed"},
    "project.export": {
        "status": "dvr-only",
        "reason": "no .drp equivalent; Premiere projects are already files",
    },
    "project.import": {"status": "dvr-only", "reason": "no .drp equivalent; use project.load"},
    "project.color_groups": {"status": "dvr-only", "reason": "no color-group API in UXP"},
    "timeline.list": {"status": "both"},
    "timeline.current": {"status": "both"},
    "timeline.inspect": {"status": "both"},
    "timeline.ensure": {"status": "both"},
    "timeline.create": {"status": "both"},
    "timeline.switch": {"status": "both"},
    "timeline.delete": {"status": "both"},
    "timeline.rename": {"status": "both"},
    "timeline.append": {"status": "both"},
    "timeline.insert": {"status": "both"},
    "timeline.clear": {"status": "both"},
    "timeline.add_title": {
        "status": "dvr-only",
        "reason": "no title/text-clip creation in UXP (use MOGRTs)",
    },
    "timeline.insert_mogrt": {
        "status": "pmr-only",
        "reason": "MOGRT insertion is a Premiere feature",
    },
    "timeline.subtitles": {"status": "dvr-only", "reason": "no caption-generation API in UXP"},
    "timeline.scene_cut_detection": {
        "status": "pmr-only",
        "reason": "SequenceUtils scene edit detection",
    },
    "marker.add": {"status": "both"},
    "marker.list": {"status": "both"},
    "marker.remove": {"status": "both"},
    "clip.where": {"status": "both"},
    "clip.rename": {"status": "both"},
    "clip.enable": {"status": "both"},
    "clip.move": {"status": "both"},
    "clip.set_properties": {
        "status": "dvr-only",
        "reason": "no generic clip-property dict; use effects.set_param",
    },
    "clip.transform": {
        "status": "both",
        "note": "pmr: effects.set_param on the Motion/Opacity components",
    },
    "effects.set_param": {"status": "pmr-only", "reason": "component params incl. keyframes"},
    "effects.list": {"status": "pmr-only", "reason": "effect factories are a Premiere UXP feature"},
    "effects.apply": {"status": "pmr-only"},
    "effects.components": {"status": "pmr-only"},
    "transition.add": {"status": "pmr-only", "reason": "dvr has no transition API"},
    "media.inspect": {"status": "both"},
    "media.bins": {"status": "both"},
    "media.ls": {"status": "both"},
    "media.import": {"status": "both"},
    "media.scan": {"status": "both"},
    "media.bin_ensure": {"status": "both"},
    "media.bin_delete": {"status": "both"},
    "media.move": {"status": "both"},
    "media.relink": {
        "status": "dvr-only",
        "reason": "per-clip changeMediaFilePath only; no batch relink",
    },
    "media.proxy": {"status": "both", "note": "pmr: attachProxy per clip"},
    "media.transcribe": {"status": "both", "note": "pmr: Transcript API (26.3+)"},
    "media.subclip": {
        "status": "pmr-only",
        "reason": "ClipProjectItem.createSubClipAction (26.3+)",
    },
    "timeline.set_in_out": {"status": "both", "note": "pmr: sequence in/out actions"},
    "render.submit": {"status": "both", "note": "pmr: .epr presets via EncoderManager"},
    "render.presets": {"status": "both", "note": "pmr discovers .epr files on disk"},
    "render.status": {"status": "both", "note": "pmr: event-driven, no job ids"},
    "render.watch": {"status": "both"},
    "render.queue": {"status": "dvr-only", "reason": "no enumerable render queue in UXP"},
    "render.formats": {"status": "dvr-only", "reason": "presets replace format/codec enums"},
    "render.codecs": {"status": "dvr-only", "reason": "presets replace format/codec enums"},
    "render.stop": {"status": "dvr-only", "reason": "no cancel API in UXP"},
    "render.clear": {"status": "dvr-only", "reason": "no queue to clear"},
    "render.export_frame": {"status": "both", "note": "pmr: Exporter.exportSequenceFrame"},
    "interchange.export": {"status": "both", "note": "pmr: fcpxml/otio/aaf (26.3+)"},
    "interchange.import": {"status": "dvr-only", "reason": "removed from UXP in 26.3"},
    "metadata.get": {"status": "pmr-only", "reason": "XMP metadata API"},
    "metadata.set": {"status": "pmr-only"},
    "source_monitor": {
        "status": "pmr-only",
        "reason": "source monitor control is Premiere-specific",
    },
    "properties.get": {"status": "pmr-only", "reason": "per-project key-value store"},
    "properties.set": {"status": "pmr-only"},
    "color.grade": {"status": "dvr-only", "reason": "no color-page equivalent in UXP"},
    "fusion": {"status": "dvr-only", "reason": "no Fusion equivalent in Premiere"},
    "gallery.stills": {"status": "dvr-only", "reason": "no gallery in Premiere"},
    "spec.apply": {"status": "both"},
    "spec.export": {"status": "both"},
    "diff.timelines": {"status": "both"},
    "diff.spec": {"status": "both"},
    "snapshot.save": {"status": "both"},
    "snapshot.restore": {"status": "both"},
    "lint": {"status": "both"},
    "eval": {"status": "both", "note": "dvr: Python; pmr: JavaScript inside Premiere"},
}


# Static — known clip properties exposed by TimelineItem.GetProperty/SetProperty.
# Source: DaVinci Resolve scripting docs, "Timeline item properties".
COMPOSITE_MODES: dict[str, int] = {
    "Normal": 0,
    "Add": 1,
    "Subtract": 2,
    "Difference": 3,
    "Multiply": 4,
    "Screen": 5,
    "Overlay": 6,
    "HardLight": 7,
    "SoftLight": 8,
    "Darken": 9,
    "Lighten": 10,
    "ColorDodge": 11,
    "ColorBurn": 12,
    "Exclusion": 13,
    "Hue": 14,
    "Saturate": 15,
    "Colorize": 16,
    "LumaMask": 17,
    "Divide": 18,
    "LinearDodge": 19,
    "LinearBurn": 20,
    "LinearLight": 21,
    "VividLight": 22,
    "PinLight": 23,
    "HardMix": 24,
    "LighterColor": 25,
    "DarkerColor": 26,
    "Foreground": 27,
    "Alpha": 28,
    "InvertedAlpha": 29,
    "Lum": 30,
    "InvertedLum": 31,
}

DYNAMIC_ZOOM_EASE: dict[str, int] = {
    "Linear": 0,
    "In": 1,
    "Out": 2,
    "InAndOut": 3,
}

RETIME_PROCESS: dict[str, int] = {
    "UseProject": 0,
    "Nearest": 1,
    "FrameBlend": 2,
    "OpticalFlow": 3,
}

MOTION_ESTIMATION: dict[str, int] = {
    "UseProject": 0,
    "StandardFaster": 1,
    "StandardBetter": 2,
    "EnhancedFaster": 3,
    "EnhancedBetter": 4,
    "SpeedWarp": 5,
}

SCALING: dict[str, int] = {
    "UseProject": 0,
    "Crop": 1,
    "Fit": 2,
    "Fill": 3,
    "Stretch": 4,
}

RESIZE_FILTERS: dict[str, int] = {
    "UseProject": 0,
    "Sharper": 1,
    "Smoother": 2,
    "Bicubic": 3,
    "Bilinear": 4,
    "Bessel": 5,
    "Box": 6,
    "CatmullRom": 7,
    "Cubic": 8,
    "Gaussian": 9,
    "Lanczos": 10,
    "Mitchell": 11,
    "NearestNeighbor": 12,
    "Quadratic": 13,
    "Sinc": 14,
    "Linear": 15,
}

CLIP_PROPERTIES: dict[str, dict[str, Any]] = {
    "Pan": {"type": "float", "range": ["-4*width", "4*width"], "group": "transform"},
    "Tilt": {"type": "float", "range": ["-4*height", "4*height"], "group": "transform"},
    "ZoomX": {"type": "float", "min": 0.0, "max": 100.0, "group": "transform"},
    "ZoomY": {"type": "float", "min": 0.0, "max": 100.0, "group": "transform"},
    "ZoomGang": {"type": "bool", "group": "transform"},
    "RotationAngle": {"type": "float", "min": -360.0, "max": 360.0, "group": "transform"},
    "AnchorPointX": {
        "type": "float",
        "range": ["-4*width", "4*width"],
        "group": "transform",
    },
    "AnchorPointY": {
        "type": "float",
        "range": ["-4*height", "4*height"],
        "group": "transform",
    },
    "Pitch": {"type": "float", "min": -1.5, "max": 1.5, "group": "transform"},
    "Yaw": {"type": "float", "min": -1.5, "max": 1.5, "group": "transform"},
    "FlipX": {"type": "bool", "group": "transform"},
    "FlipY": {"type": "bool", "group": "transform"},
    "CropLeft": {"type": "float", "min": 0.0, "max_label": "width", "group": "crop"},
    "CropRight": {"type": "float", "min": 0.0, "max_label": "width", "group": "crop"},
    "CropTop": {"type": "float", "min": 0.0, "max_label": "height", "group": "crop"},
    "CropBottom": {"type": "float", "min": 0.0, "max_label": "height", "group": "crop"},
    "CropSoftness": {"type": "float", "min": -100.0, "max": 100.0, "group": "crop"},
    "CropRetain": {"type": "bool", "group": "crop"},
    "DynamicZoomEase": {
        "type": "enum",
        "values": list(DYNAMIC_ZOOM_EASE),
        "constants": DYNAMIC_ZOOM_EASE,
        "group": "dynamic_zoom",
    },
    "CompositeMode": {
        "type": "enum",
        "values": list(COMPOSITE_MODES),
        "constants": COMPOSITE_MODES,
        "group": "composite",
    },
    "Opacity": {"type": "float", "min": 0.0, "max": 100.0, "group": "composite"},
    "Distortion": {"type": "float", "min": -1.0, "max": 1.0, "group": "composite"},
    "RetimeProcess": {
        "type": "enum",
        "values": list(RETIME_PROCESS),
        "constants": RETIME_PROCESS,
        "group": "retime",
    },
    "MotionEstimation": {
        "type": "enum",
        "values": list(MOTION_ESTIMATION),
        "constants": MOTION_ESTIMATION,
        "group": "retime",
    },
    "Scaling": {
        "type": "enum",
        "values": list(SCALING),
        "constants": SCALING,
        "group": "scaling",
    },
    "ResizeFilter": {
        "type": "enum",
        "values": list(RESIZE_FILTERS),
        "constants": RESIZE_FILTERS,
        "group": "scaling",
    },
}

CLIP_PROPERTY_DEFAULTS: dict[str, Any] = {
    "Pan": 0.0,
    "Tilt": 0.0,
    "ZoomX": 1.0,
    "ZoomY": 1.0,
    "ZoomGang": True,
    "RotationAngle": 0.0,
    "AnchorPointX": 0.0,
    "AnchorPointY": 0.0,
    "Pitch": 0.0,
    "Yaw": 0.0,
    "FlipX": False,
    "FlipY": False,
    "CropLeft": 0.0,
    "CropRight": 0.0,
    "CropTop": 0.0,
    "CropBottom": 0.0,
    "CropSoftness": 0.0,
    "CropRetain": False,
    "DynamicZoomEase": 0,
    "CompositeMode": 0,
    "Opacity": 100.0,
    "Distortion": 0.0,
    "RetimeProcess": 0,
    "MotionEstimation": 0,
    "Scaling": 0,
    "ResizeFilter": 0,
}

CLIP_PROPERTY_GROUPS: dict[str, tuple[str, ...]] = {
    "transform": (
        "Pan",
        "Tilt",
        "ZoomX",
        "ZoomY",
        "ZoomGang",
        "RotationAngle",
        "AnchorPointX",
        "AnchorPointY",
        "Pitch",
        "Yaw",
        "FlipX",
        "FlipY",
    ),
    "crop": (
        "CropLeft",
        "CropRight",
        "CropTop",
        "CropBottom",
        "CropSoftness",
        "CropRetain",
    ),
    "dynamic_zoom": ("DynamicZoomEase",),
    "composite": ("CompositeMode", "Opacity", "Distortion"),
    "retime": ("RetimeProcess", "MotionEstimation"),
    "scaling": ("Scaling", "ResizeFilter"),
}


def _token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key in CLIP_PROPERTIES:
        aliases[_token(key)] = key
    aliases.update(
        {
            "positionx": "Pan",
            "x": "Pan",
            "panx": "Pan",
            "positiony": "Tilt",
            "y": "Tilt",
            "tilty": "Tilt",
            "zoom": "ZoomX",
            "scale": "ZoomX",
            "scalex": "ZoomX",
            "scaley": "ZoomY",
            "rotation": "RotationAngle",
            "rotate": "RotationAngle",
            "angle": "RotationAngle",
            "anchorx": "AnchorPointX",
            "anchory": "AnchorPointY",
            "fliphorizontal": "FlipX",
            "flipvertical": "FlipY",
            "left": "CropLeft",
            "right": "CropRight",
            "top": "CropTop",
            "bottom": "CropBottom",
            "softness": "CropSoftness",
            "retain": "CropRetain",
            "dynamiczoomeasing": "DynamicZoomEase",
            "easing": "DynamicZoomEase",
            "composite": "CompositeMode",
            "compositemode": "CompositeMode",
            "blendmode": "CompositeMode",
            "blend": "CompositeMode",
            "retime": "RetimeProcess",
            "retimeprocess": "RetimeProcess",
            "motion": "MotionEstimation",
            "motionest": "MotionEstimation",
            "motionestimation": "MotionEstimation",
            "resize": "ResizeFilter",
            "resizefilter": "ResizeFilter",
        }
    )
    return aliases


CLIP_PROPERTY_ALIASES: dict[str, str] = _alias_map()


def normalize_clip_property_key(key: str) -> str:
    """Return the Resolve property key for a friendly or canonical name."""
    try:
        return CLIP_PROPERTY_ALIASES[_token(key)]
    except KeyError as exc:
        raise errors.ClipError(
            f"Unknown timeline-item property {key!r}.",
            fix="Inspect valid keys and aliases with `dvr schema clip-properties`.",
            state={"requested": key, "available": sorted(CLIP_PROPERTIES)},
        ) from exc


def _enum_aliases(constants: dict[str, int]) -> dict[str, int]:
    aliases: dict[str, int] = {}
    for name, value in constants.items():
        aliases[_token(name)] = value
    aliases.update(
        {
            "useprojectsetting": constants.get("UseProject", 0),
            "project": constants.get("UseProject", 0),
            "none": constants.get("UseProject", 0),
            "nearestframe": constants.get("Nearest", 1),
            "standard": constants.get("StandardBetter", constants.get("StandardFaster", 0)),
            "enhanced": constants.get("EnhancedBetter", constants.get("EnhancedFaster", 0)),
            "speedwarp": constants.get("SpeedWarp", 5),
            "boxfilter": constants.get("Box", 6),
            "catmullrom": constants.get("CatmullRom", 7),
            "nearest": constants.get("NearestNeighbor", constants.get("Nearest", 1)),
            "saturation": constants.get("Saturate", 15),
            "color": constants.get("Colorize", 16),
            "luminosity": constants.get("Lum", 30),
            "luma": constants.get("Lum", 30),
            "difference": constants.get("Difference", 3),
            "diff": constants.get("Difference", 3),
            "inandout": constants.get("InAndOut", 3),
            "inout": constants.get("InAndOut", 3),
        }
    )
    return {key: value for key, value in aliases.items() if value is not None}


def coerce_clip_property_value(key: str, value: Any) -> Any:
    """Coerce and validate a value for a documented timeline-item property."""
    prop = normalize_clip_property_key(key)
    meta = CLIP_PROPERTIES[prop]
    kind = meta["type"]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "yes", "on", "1"):
                return True
            if lowered in ("false", "no", "off", "0"):
                return False
        raise errors.ClipError(
            f"Timeline-item property {prop} expects a boolean value.",
            state={"key": prop, "value": value},
        )
    if kind == "float":
        try:
            coerced = float(value)
        except (TypeError, ValueError) as exc:
            raise errors.ClipError(
                f"Timeline-item property {prop} expects a numeric value.",
                state={"key": prop, "value": value},
            ) from exc
        if "min" in meta and coerced < float(meta["min"]):
            raise errors.ClipError(
                f"Timeline-item property {prop} must be >= {meta['min']}.",
                state={"key": prop, "value": value, "min": meta["min"]},
            )
        if "max" in meta and coerced > float(meta["max"]):
            raise errors.ClipError(
                f"Timeline-item property {prop} must be <= {meta['max']}.",
                state={"key": prop, "value": value, "max": meta["max"]},
            )
        return coerced
    if kind == "enum":
        constants = dict(meta["constants"])
        if isinstance(value, bool):
            raise errors.ClipError(
                f"Timeline-item property {prop} expects an enum name or integer constant.",
                state={"key": prop, "value": value},
            )
        if isinstance(value, int):
            if value in constants.values():
                return value
            raise errors.ClipError(
                f"Timeline-item property {prop} received unsupported enum value {value!r}.",
                state={"key": prop, "value": value, "valid": constants},
            )
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lstrip("-").isdigit():
                return coerce_clip_property_value(prop, int(stripped))
            aliases = _enum_aliases(constants)
            token = _token(stripped)
            if token in aliases:
                return aliases[token]
        raise errors.ClipError(
            f"Timeline-item property {prop} received unsupported enum value {value!r}.",
            fix=f"Use one of: {', '.join(constants)} or its integer constant.",
            state={"key": prop, "value": value, "valid": constants},
        )
    return value


def normalize_clip_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Normalize friendly keys and values into Resolve SetProperty payloads."""
    normalized: dict[str, Any] = {}
    for key, value in properties.items():
        prop = normalize_clip_property_key(str(key))
        if prop == "ZoomX" and _token(str(key)) in ("zoom", "scale"):
            normalized["ZoomX"] = coerce_clip_property_value("ZoomX", value)
            normalized["ZoomY"] = coerce_clip_property_value("ZoomY", value)
            continue
        normalized[prop] = coerce_clip_property_value(prop, value)
    return normalized


def reset_clip_properties(groups: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """Return default SetProperty values for one or more property groups."""
    requested = tuple(groups or CLIP_PROPERTY_GROUPS.keys())
    props: dict[str, Any] = {}
    for group in requested:
        try:
            keys = CLIP_PROPERTY_GROUPS[group]
        except KeyError as exc:
            raise errors.ClipError(
                f"Unknown clip property reset group {group!r}.",
                fix=f"Use one of: {', '.join(CLIP_PROPERTY_GROUPS)}.",
                state={"requested": group},
            ) from exc
        for key in keys:
            props[key] = CLIP_PROPERTY_DEFAULTS[key]
    return props


def clip_property_capabilities() -> dict[str, Any]:
    """Describe what Resolve exposes for timeline-item editing."""
    return {
        "static_properties": {
            "supported": True,
            "method": "TimelineItem.SetProperty",
            "properties": sorted(CLIP_PROPERTIES),
            "groups": {group: list(keys) for group, keys in CLIP_PROPERTY_GROUPS.items()},
        },
        "clip_placement": {
            "supported": True,
            "method": "MediaPool.AppendToTimeline",
            "notes": [
                "Supports source start/end frames, media type, target track index, and record frame.",
                "Moving an existing item while preserving effects is not exposed; rebuild by delete + append.",
            ],
        },
        "fusion_comps": {
            "supported": True,
            "method": "TimelineItem.AddFusionComp/ImportFusionComp/ExportFusionComp",
            "notes": ["Fusion animation can be imported as a comp, but DVR does not author node graphs yet."],
        },
        "transitions": {
            "supported": False,
            "reason": "Resolve scripting does not expose reliable edit-page transition creation APIs.",
        },
        "keyframe_animation": {
            "supported": False,
            "reason": "Resolve scripting does not expose general keyframe writes for timeline-item properties.",
            "partial_read_only": [
                "Stereo convergence/floating-window keyframe reads",
                "DRX grade keyframe application modes",
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
    "colorSpaceOutput": {
        "type": "enum-or-string",
        "common": ["Same as Timeline", "Rec.709", "Rec.2020", "P3-D65"],
        "notes": [
            "Some Resolve builds reject a raw gamut like 'Rec.2020' here and expect 'Same as Timeline'."
        ],
    },
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
    "separateColorSpaceAndGamma": {
        "type": "bool-string",
        "values": ["0", "1"],
        "notes": [
            "Set to '1' before applying separate colorSpace* and colorSpace*Gamma values.",
            "When '0', Resolve may store combined values such as 'Rec.709 Gamma 2.4'.",
        ],
    },
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
    if topic == "parity":
        return {"operations": PARITY, "statuses": ["both", "dvr-only", "pmr-only"]}
    if topic == "clip-properties":
        return CLIP_PROPERTIES
    if topic == "clip-property-aliases":
        return dict(sorted(CLIP_PROPERTY_ALIASES.items()))
    if topic == "clip-property-defaults":
        return dict(CLIP_PROPERTY_DEFAULTS)
    if topic == "clip-capabilities":
        return clip_property_capabilities()
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
        "Available: parity, clip-properties, clip-property-aliases, clip-property-defaults, "
        "clip-capabilities, settings, export-formats, color-presets, render-formats, "
        "render-codecs, render-presets."
    )


TOPICS: tuple[str, ...] = (
    "parity",
    "clip-properties",
    "clip-property-aliases",
    "clip-property-defaults",
    "clip-capabilities",
    "settings",
    "export-formats",
    "color-presets",
    "render-formats",
    "render-codecs",
    "render-presets",
)


__all__ = [
    "CLIP_PROPERTIES",
    "CLIP_PROPERTY_ALIASES",
    "CLIP_PROPERTY_DEFAULTS",
    "CLIP_PROPERTY_GROUPS",
    "COMPOSITE_MODES",
    "DYNAMIC_ZOOM_EASE",
    "MOTION_ESTIMATION",
    "PARITY",
    "PROJECT_SETTINGS",
    "RESIZE_FILTERS",
    "RETIME_PROCESS",
    "SCALING",
    "TOPICS",
    "clip_property_capabilities",
    "coerce_clip_property_value",
    "color_presets",
    "export_formats",
    "get_topic",
    "normalize_clip_properties",
    "normalize_clip_property_key",
    "render_codec_matrix",
    "render_codecs",
    "render_formats",
    "render_presets",
    "reset_clip_properties",
]
