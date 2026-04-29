"""Declarative state — YAML/JSON specs reconciled against live Resolve state.

Inspired by ``kubectl apply``: describe the desired state of a project
(timelines, color settings, render presets) in a single file, then run
``dvr apply`` to bring Resolve in line. The engine computes a structured
plan first; you can preview it (``--dry-run``) before applying.

Spec schema (informal)
----------------------

::

    project: MyShow
    color_preset: rec2020_pq_4000              # optional
    settings:                                  # optional, raw key/value
      timelineFrameRate: "24"
    timelines:
      - name: Edit_v2
        fps: 24
        markers:                               # optional
          - {frame: 0, color: Blue, name: HEAD}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors
from .resolve import Resolve

logger = logging.getLogger("dvr.spec")


# ---------------------------------------------------------------------------
# Color presets for the most common HDR / SDR project setups.
# ---------------------------------------------------------------------------

# DaVinci YRGB Color Managed v2 presets — fully API-settable end to end.
# ACES presets below set color science mode + AP1 working space; their
# Input/Output Transforms (IDT/ODT) must be selected in the Resolve UI
# because Resolve's API rejects HDR PQ IDT/ODT strings (every documented
# format we've tried — UI labels like ``P3-D65 ST2084 (4000 nits)``,
# ACES 1.x ``InvRRTODT.Academy.*``, ACES 2.0 ``InvOutput.Academy.*``,
# and Resolve's internal binary names — is silently rejected by
# ``Project.SetSetting``). Set IDT/ODT in Project Settings → Color
# Management after applying these presets.
COLOR_PRESETS: dict[str, dict[str, str]] = {
    "rec2020_pq_4000": {
        "colorScienceMode": "davinciYRGBColorManagedv2",
        "isAutoColorManage": "0",
        "separateColorSpaceAndGamma": "1",
        "colorSpaceInput": "Rec.2020",
        "colorSpaceInputGamma": "ST2084",
        "colorSpaceTimeline": "Rec.2020",
        "colorSpaceTimelineGamma": "Rec.2100 ST2084",
        "colorSpaceOutput": "Rec.2020",
        "colorSpaceOutputGamma": "Rec.2100 ST2084",
        "timelineWorkingLuminanceMode": "HDR 4000",
        "hdrMasteringLuminanceMax": "4000",
        "hdrMasteringOn": "1",
        "inputDRT": "None",
        "outputDRT": "None",
    },
    "p3d65_pq_1000": {
        "colorScienceMode": "davinciYRGBColorManagedv2",
        "isAutoColorManage": "0",
        "separateColorSpaceAndGamma": "1",
        "colorSpaceInput": "P3-D65",
        "colorSpaceInputGamma": "ST2084",
        "colorSpaceTimeline": "P3-D65",
        "colorSpaceTimelineGamma": "Rec.2100 ST2084",
        "colorSpaceOutput": "P3-D65",
        "colorSpaceOutputGamma": "Rec.2100 ST2084",
        "timelineWorkingLuminanceMode": "HDR 1000",
        "hdrMasteringLuminanceMax": "1000",
        "hdrMasteringOn": "1",
    },
    "rec709_gamma24": {
        "colorScienceMode": "davinciYRGB",
        "colorSpaceInput": "Rec.709",
        "colorSpaceInputGamma": "Gamma 2.4",
        "colorSpaceTimeline": "Rec.709",
        "colorSpaceTimelineGamma": "Gamma 2.4",
        "colorSpaceOutput": "Rec.709",
        "colorSpaceOutputGamma": "Gamma 2.4",
        "hdrMasteringOn": "0",
    },
    # --- ACES presets -----------------------------------------------------
    # Color science = ACEScct (AP1 log working space, AP1 primaries shared
    # with ACEScg). Set IDT/ODT in the UI after applying — see comment above.
    "aces_p3d65_pq_4000": {
        "colorScienceMode": "acescct",
        "colorAcesNodeLUTProcessingSpace": "acesccAp1",
        "colorAcesGamutCompressType": "None",
        # Hint settings — used by some Resolve versions to size HDR UI
        # without affecting the ACES pipeline itself.
        "timelineWorkingLuminanceMode": "HDR 4000",
        "hdrMasteringLuminanceMax": "4000",
    },
    "aces_p3d65_pq_1000": {
        "colorScienceMode": "acescct",
        "colorAcesNodeLUTProcessingSpace": "acesccAp1",
        "colorAcesGamutCompressType": "None",
        "timelineWorkingLuminanceMode": "HDR 1000",
        "hdrMasteringLuminanceMax": "1000",
    },
    "aces_rec2020_pq_4000": {
        "colorScienceMode": "acescct",
        "colorAcesNodeLUTProcessingSpace": "acesccAp1",
        "colorAcesGamutCompressType": "None",
        "timelineWorkingLuminanceMode": "HDR 4000",
        "hdrMasteringLuminanceMax": "4000",
    },
    "aces_rec2020_pq_1000": {
        "colorScienceMode": "acescct",
        "colorAcesNodeLUTProcessingSpace": "acesccAp1",
        "colorAcesGamutCompressType": "None",
        "timelineWorkingLuminanceMode": "HDR 1000",
        "hdrMasteringLuminanceMax": "1000",
    },
    "aces_rec709": {
        "colorScienceMode": "acescct",
        "colorAcesNodeLUTProcessingSpace": "acesccAp1",
        "colorAcesGamutCompressType": "None",
    },
}


# Order matters for HDR / DaVinci Color Managed / ACES modes — the first
# key enables the color management framework before the rest take effect.
SETTINGS_ORDER: tuple[str, ...] = (
    "colorScienceMode",
    "isAutoColorManage",
    "separateColorSpaceAndGamma",
    "colorSpaceInput",
    "colorSpaceInputGamma",
    "colorSpaceTimeline",
    "colorSpaceTimelineGamma",
    "colorSpaceOutput",
    "colorSpaceOutputGamma",
    # ACES-specific (no-op when colorScienceMode is YRGB)
    "colorAcesNodeLUTProcessingSpace",
    "colorAcesGamutCompressType",
    "colorAcesIDT",
    "colorAcesODT",
    # HDR / DRT
    "timelineWorkingLuminanceMode",
    "hdrMasteringLuminanceMax",
    "hdrMasteringOn",
    "inputDRT",
    "outputDRT",
)


# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------


@dataclass
class TimelineSpec:
    name: str
    fps: float | None = None
    settings: dict[str, str] = field(default_factory=dict)
    markers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Hook:
    """A shell command run before or after the main reconciliation."""

    when: str  # "before" | "after"
    command: str  # shell command (run via /bin/sh -c)
    name: str = ""


@dataclass
class Spec:
    project: str
    color_preset: str | None = None
    settings: dict[str, str] = field(default_factory=dict)
    timelines: list[TimelineSpec] = field(default_factory=list)
    hooks: list[Hook] = field(default_factory=list)


def load_spec(path: str | Path) -> Spec:
    """Load a YAML or JSON spec file."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise errors.SpecError(
            f"Spec file not found: {p}",
            fix="Create the file or pass an absolute path.",
            state={"path": str(p)},
        )
    text = p.read_text(encoding="utf-8")
    data: dict[str, Any]
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    return parse_spec(data)


def parse_spec(data: dict[str, Any]) -> Spec:
    """Parse a dict into a :class:`Spec`."""
    if "project" not in data:
        raise errors.SpecError(
            "Spec is missing required 'project' field.",
            fix="Add `project: <name>` at the top of the spec.",
        )
    timelines: list[TimelineSpec] = []
    for entry in data.get("timelines", []) or []:
        if "name" not in entry:
            raise errors.SpecError(
                "Each timeline entry requires a 'name' field.",
                state={"entry": entry},
            )
        timelines.append(
            TimelineSpec(
                name=str(entry["name"]),
                fps=float(entry["fps"]) if "fps" in entry else None,
                settings=dict(entry.get("settings", {}) or {}),
                markers=list(entry.get("markers", []) or []),
            )
        )
    color_preset = data.get("color_preset")
    if color_preset and color_preset not in COLOR_PRESETS:
        raise errors.SpecError(
            f"Unknown color_preset {color_preset!r}.",
            fix=f"Use one of: {', '.join(COLOR_PRESETS)}",
        )
    hooks: list[Hook] = []
    raw_hooks = data.get("hooks", {}) or {}
    if isinstance(raw_hooks, dict):
        for when_key in ("before", "after"):
            for entry in raw_hooks.get(when_key, []) or []:
                if isinstance(entry, str):
                    hooks.append(Hook(when=when_key, command=entry))
                elif isinstance(entry, dict) and "command" in entry:
                    hooks.append(
                        Hook(
                            when=when_key,
                            command=str(entry["command"]),
                            name=str(entry.get("name", "")),
                        )
                    )
    return Spec(
        project=str(data["project"]),
        color_preset=color_preset,
        settings=dict(data.get("settings", {}) or {}),
        timelines=timelines,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """A single change the engine will (or did) apply."""

    op: str  # "create" | "update" | "noop" | "set"
    target: str  # e.g. "project:MyShow"
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


def plan(spec: Spec, resolve: Resolve) -> list[Action]:
    """Compute the actions required to bring Resolve in line with ``spec``."""
    actions: list[Action] = []

    # Project
    existing_projects = resolve.project.list()
    if spec.project in existing_projects:
        actions.append(Action(op="noop", target=f"project:{spec.project}", detail="already exists"))
    else:
        actions.append(Action(op="create", target=f"project:{spec.project}"))

    # Project-level settings: color preset → settings overlay
    desired_settings: dict[str, str] = {}
    if spec.color_preset:
        desired_settings.update(COLOR_PRESETS[spec.color_preset])
    desired_settings.update(spec.settings)

    # We can only diff settings if the project already exists or after we
    # create it; for plan output we list them as "set".
    for key, value in desired_settings.items():
        actions.append(
            Action(
                op="set",
                target=f"project:{spec.project}/setting:{key}",
                detail=f"= {value}",
                payload={"key": key, "value": value},
            )
        )

    # Timelines
    for tl in spec.timelines:
        actions.append(
            Action(
                op="ensure",
                target=f"timeline:{tl.name}",
                detail=f"in project {spec.project}",
                payload={"name": tl.name},
            )
        )
        for key, value in tl.settings.items():
            actions.append(
                Action(
                    op="set",
                    target=f"timeline:{tl.name}/setting:{key}",
                    detail=f"= {value}",
                    payload={"key": key, "value": value, "timeline": tl.name},
                )
            )
        for marker in tl.markers:
            actions.append(
                Action(
                    op="set",
                    target=f"timeline:{tl.name}/marker:{marker.get('frame', 0)}",
                    detail=marker.get("name", ""),
                    payload={"timeline": tl.name, "marker": marker},
                )
            )

    return actions


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _run_hooks(hooks: list[Hook], when: str, *, env: dict[str, str] | None = None) -> None:
    import subprocess

    for hook in hooks:
        if hook.when != when:
            continue
        subprocess.run(
            hook.command,
            shell=True,
            check=True,
            env={**(env or {})} if env else None,
        )


def apply(
    spec: Spec,
    resolve: Resolve,
    *,
    dry_run: bool = False,
    run_hooks: bool = True,
) -> list[Action]:
    """Reconcile the live Resolve state to match ``spec``."""
    actions = plan(spec, resolve)
    if dry_run:
        return actions

    if run_hooks:
        _run_hooks(spec.hooks, "before")

    # Project — get-or-create.
    project = resolve.project.ensure(spec.project)

    # Project-level settings, ordered so HDR setup works.
    desired: dict[str, str] = {}
    if spec.color_preset:
        desired.update(COLOR_PRESETS[spec.color_preset])
    desired.update(spec.settings)

    for key in SETTINGS_ORDER:
        if key in desired:
            project.set_setting(key, desired[key])
    for key, value in desired.items():
        if key not in SETTINGS_ORDER:
            project.set_setting(key, value)

    # Timelines.
    for tl_spec in spec.timelines:
        tl = project.timeline.ensure(tl_spec.name)
        for key, value in tl_spec.settings.items():
            tl.set_setting(key, value)
        if tl_spec.fps is not None:
            tl.set_setting("timelineFrameRate", str(tl_spec.fps))
        existing_markers = tl.markers()
        for marker in tl_spec.markers:
            frame = int(marker.get("frame", 0))
            if frame not in existing_markers:
                tl.add_marker(
                    frame=frame,
                    color=str(marker.get("color", "Blue")),
                    name=str(marker.get("name", "")),
                    note=str(marker.get("note", "")),
                    duration=int(marker.get("duration", 1)),
                    custom_data=str(marker.get("custom_data", "")),
                )

    if run_hooks:
        _run_hooks(spec.hooks, "after")

    return actions


__all__ = [
    "COLOR_PRESETS",
    "Action",
    "Hook",
    "Spec",
    "TimelineSpec",
    "apply",
    "load_spec",
    "parse_spec",
    "plan",
]
