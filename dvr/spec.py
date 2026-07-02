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
    bins:                                      # optional, nested "A/B/C" paths
      - Footage/Day01
      - Audio
    timelines:
      - name: Edit_v2
        fps: 24
        tracks:                                # optional, minimum track counts
          video: 3
          audio: 4
        markers:                               # optional
          - {frame: 0, color: Blue, name: HEAD}
        titles:                                # optional
          - text: "OPENING TITLE"              # required; also the idempotency key
            at: "01:00:02:00"                  # optional timecode to place it
            font: "Open Sans"
            size: 0.12
            color: "#ffcc00"
            align: center

Applying supports two safety levers:

* ``transactional=True`` — capture a snapshot of the project before
  mutating; on any failure, restore it and report the rollback.
* ``verify=True`` — read every setting back after writing it and fail
  loudly when Resolve silently ignored the write.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
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
class ClipOperationSpec:
    selector: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)


# Text+ styling keys accepted in a title spec, forwarded to ItemText.set().
_TITLE_STYLE_KEYS: tuple[str, ...] = (
    "font",
    "style",
    "size",
    "color",
    "opacity",
    "tracking",
    "line_spacing",
    "position",
    "align",
    "vertical_align",
)


@dataclass
class TitleSpec:
    """A desired on-screen title, identified by its text for idempotency."""

    text: str
    title: str = "Text+"
    at: str | None = None
    fusion: bool = True
    styling: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimelineSpec:
    name: str
    fps: float | None = None
    settings: dict[str, str] = field(default_factory=dict)
    markers: list[dict[str, Any]] = field(default_factory=list)
    clip_properties: list[ClipOperationSpec] = field(default_factory=list)
    titles: list[TitleSpec] = field(default_factory=list)
    tracks: dict[str, int] = field(default_factory=dict)  # minimum counts by type


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
    bins: list[str] = field(default_factory=list)  # nested "A/B/C" paths
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
    project = data["project"]
    if not isinstance(project, str) or not project.strip():
        raise errors.SpecError(
            "Spec field 'project' must be a non-empty string.",
            cause="The spec parser accepts `project: <name>`, not a mapping or object.",
            fix="Use `project: MyProject` at the top of the spec.",
            state={"project": project},
        )
    timelines: list[TimelineSpec] = []
    for entry in data.get("timelines", []) or []:
        if not isinstance(entry, dict):
            raise errors.SpecError(
                "Each timeline entry must be an object.",
                state={"entry": entry},
            )
        if "name" not in entry:
            raise errors.SpecError(
                "Each timeline entry requires a 'name' field.",
                state={"entry": entry},
            )
        clip_properties = _parse_clip_property_specs(entry)
        titles = _parse_title_specs(entry)
        timelines.append(
            TimelineSpec(
                name=str(entry["name"]),
                fps=float(entry["fps"]) if "fps" in entry else None,
                settings=dict(entry.get("settings", {}) or {}),
                markers=list(entry.get("markers", []) or []),
                clip_properties=clip_properties,
                titles=titles,
                tracks=_parse_tracks(entry),
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
    bins = data.get("bins", []) or []
    if not isinstance(bins, list) or not all(isinstance(b, str) for b in bins):
        raise errors.SpecError(
            "Spec field 'bins' must be a list of bin path strings.",
            fix="Use nested paths like `bins: [Footage/Day01, Audio]`.",
            state={"bins": bins},
        )
    return Spec(
        project=project,
        color_preset=color_preset,
        settings=dict(data.get("settings", {}) or {}),
        bins=[str(b) for b in bins],
        timelines=timelines,
        hooks=hooks,
    )


def _parse_tracks(entry: dict[str, Any]) -> dict[str, int]:
    raw = entry.get("tracks", {}) or {}
    if not isinstance(raw, dict):
        raise errors.SpecError(
            "Timeline tracks must be a mapping of track type to minimum count.",
            fix="Use `tracks: {video: 3, audio: 4}`.",
            state={"timeline": entry.get("name"), "tracks": raw},
        )
    tracks: dict[str, int] = {}
    for track_type, count in raw.items():
        if str(track_type) not in ("video", "audio", "subtitle"):
            raise errors.SpecError(
                f"Unknown track type {track_type!r} in timeline tracks.",
                fix="Use video, audio, or subtitle.",
                state={"timeline": entry.get("name"), "tracks": raw},
            )
        tracks[str(track_type)] = int(count)
    return tracks


def _parse_clip_property_specs(entry: dict[str, Any]) -> list[ClipOperationSpec]:
    from . import schema as schema_mod

    raw_ops = entry.get("clip_properties", entry.get("clips", [])) or []
    if not isinstance(raw_ops, list):
        raise errors.SpecError(
            "Timeline clip property operations must be a list.",
            state={"timeline": entry.get("name"), "clip_properties": raw_ops},
        )
    parsed: list[ClipOperationSpec] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            raise errors.SpecError(
                "Each clip property operation must be an object.",
                state={"timeline": entry.get("name"), "operation": raw},
            )
        props = raw.get("properties")
        if not isinstance(props, dict) or not props:
            raise errors.SpecError(
                "Each clip property operation requires a non-empty properties mapping.",
                state={"timeline": entry.get("name"), "operation": raw},
            )
        selector = raw.get("selector", raw.get("where", {})) or {}
        if not isinstance(selector, dict):
            raise errors.SpecError(
                "Clip property selector must be an object.",
                state={"timeline": entry.get("name"), "selector": selector},
            )
        parsed.append(
            ClipOperationSpec(
                selector=dict(selector),
                properties=schema_mod.normalize_clip_properties(dict(props)),
            )
        )
    return parsed


def _parse_title_specs(entry: dict[str, Any]) -> list[TitleSpec]:
    raw_titles = entry.get("titles", []) or []
    if not isinstance(raw_titles, list):
        raise errors.SpecError(
            "Timeline titles must be a list.",
            state={"timeline": entry.get("name"), "titles": raw_titles},
        )
    parsed: list[TitleSpec] = []
    for raw in raw_titles:
        if not isinstance(raw, dict):
            raise errors.SpecError(
                "Each title must be an object.",
                state={"timeline": entry.get("name"), "title": raw},
            )
        if "text" not in raw or not str(raw["text"]).strip():
            raise errors.SpecError(
                "Each title requires a non-empty 'text' field.",
                cause="Titles are matched by their text for idempotent re-runs.",
                state={"timeline": entry.get("name"), "title": raw},
            )
        styling = {key: raw[key] for key in _TITLE_STYLE_KEYS if key in raw}
        parsed.append(
            TitleSpec(
                text=str(raw["text"]),
                title=str(raw.get("title", "Text+")),
                at=str(raw["at"]) if raw.get("at") is not None else None,
                fusion=bool(raw.get("fusion", True)),
                styling=styling,
            )
        )
    return parsed


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

    # Bins (nested paths, idempotent).
    for bin_path in spec.bins:
        actions.append(
            Action(
                op="ensure",
                target=f"bin:{bin_path}",
                detail=f"in project {spec.project}",
                payload={"path": bin_path},
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
        for track_type, count in sorted(tl.tracks.items()):
            actions.append(
                Action(
                    op="ensure",
                    target=f"timeline:{tl.name}/tracks:{track_type}",
                    detail=f">= {count}",
                    payload={"timeline": tl.name, "track_type": track_type, "count": count},
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
        for operation in tl.clip_properties:
            actions.append(
                Action(
                    op="set",
                    target=f"timeline:{tl.name}/clip-properties:{_selector_label(operation.selector)}",
                    detail=", ".join(f"{k}={v}" for k, v in operation.properties.items()),
                    payload={
                        "timeline": tl.name,
                        "selector": operation.selector,
                        "properties": operation.properties,
                    },
                )
            )
        for title in tl.titles:
            actions.append(
                Action(
                    op="ensure",
                    target=f"timeline:{tl.name}/title:{title.text}",
                    detail=", ".join(f"{k}={v}" for k, v in sorted(title.styling.items())),
                    payload={
                        "timeline": tl.name,
                        "text": title.text,
                        "title": title.title,
                        "at": title.at,
                        "styling": title.styling,
                    },
                )
            )

    return actions


def _find_text_item(timeline: Any, text: str) -> Any | None:
    """Return the first video title whose text matches ``text`` (idempotency key)."""
    for item in timeline.items("video"):
        try:
            if item.is_text and str(item.text.value) == str(text):
                return item
        except Exception:  # boundary
            continue
    return None


def _selector_label(selector: dict[str, Any]) -> str:
    if not selector:
        return "all"
    return ",".join(f"{key}={value}" for key, value in sorted(selector.items()))


def _select_timeline_items(timeline: Any, selector: dict[str, Any]) -> list[Any]:
    track_type = selector.get("track_type")
    track_index = selector.get("track_index")
    if track_type and track_index is not None:
        items = list(timeline.track(str(track_type), int(track_index)).items)
    elif track_type:
        items = list(timeline.items(str(track_type)))
    else:
        items = list(timeline.items())

    def matches(item: Any) -> bool:
        if track_index is not None and int(item.track_index) != int(track_index):
            return False
        if selector.get("name") is not None and item.name != selector["name"]:
            return False
        if selector.get("name_contains") is not None and selector["name_contains"] not in item.name:
            return False
        if selector.get("start") is not None and int(item.start) != int(selector["start"]):
            return False
        if selector.get("end") is not None and int(item.end) != int(selector["end"]):
            return False
        if selector.get("duration_lt") is not None and not item.duration < int(
            selector["duration_lt"]
        ):
            return False
        return not (
            selector.get("duration_gt") is not None
            and not item.duration > int(selector["duration_gt"])
        )

    return [item for item in items if matches(item)]


def _same_property_value(current: Any, desired: Any) -> bool:
    return current == desired or str(current) == str(desired)


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
    continue_on_error: bool = False,
    transactional: bool = False,
    verify: bool = False,
) -> list[Action]:
    """Reconcile the live Resolve state to match ``spec``.

    Args:
        dry_run:           Compute and return the plan without mutating.
        run_hooks:         Run the spec's before/after shell hooks.
        continue_on_error: Collect per-action failures instead of stopping
                           at the first one (a summary error is still
                           raised at the end).
        transactional:     Capture a snapshot of the project before
                           mutating; on failure, restore it and raise a
                           ``SpecError`` describing the rollback. When the
                           project doesn't exist yet there is nothing to
                           roll back to, and the error says so.
        verify:            Read every setting back after writing it and
                           raise ``SettingsError`` when Resolve silently
                           ignored the write.
    """
    actions = plan(spec, resolve)
    if dry_run:
        return actions

    if run_hooks:
        _run_hooks(spec.hooks, "before")

    pre_snapshot = None
    if transactional and spec.project in resolve.project.list():
        from . import snapshot as snapshot_mod

        resolve.project.ensure(spec.project)
        pre_snapshot = snapshot_mod.capture(resolve, name=f"pre-apply-{spec.project}")
        snapshot_mod.save(pre_snapshot)

    try:
        _apply_mutations(
            spec,
            resolve,
            continue_on_error=continue_on_error,
            verify=verify,
        )
    except errors.DvrError as exc:
        if pre_snapshot is None:
            raise
        from . import snapshot as snapshot_mod

        try:
            snapshot_mod.restore(resolve, pre_snapshot)
        except errors.DvrError as rollback_exc:
            raise errors.SpecError(
                "Apply failed AND the automatic rollback failed.",
                cause=exc.message,
                fix=(
                    f"Restore manually with `dvr snapshot restore {pre_snapshot.name!r}` "
                    "after fixing the rollback error."
                ),
                state={
                    "snapshot": pre_snapshot.name,
                    "apply_error": exc.to_dict(),
                    "rollback_error": rollback_exc.to_dict(),
                },
            ) from exc
        raise errors.SpecError(
            f"Apply failed; project rolled back to snapshot {pre_snapshot.name!r}.",
            cause=exc.message,
            fix="Fix the failing action below and re-run the same spec.",
            state={"snapshot": pre_snapshot.name, "apply_error": exc.to_dict()},
        ) from exc

    if run_hooks:
        _run_hooks(spec.hooks, "after")

    return actions


def _verified_set_setting(target: Any, key: str, value: Any) -> None:
    """``set_setting`` + read-back check. Fails loudly on silent rejection."""
    target.set_setting(key, value)
    try:
        read_back = target.get_setting(key)
    except errors.DvrError:
        return  # can't read back — treat the successful write as final
    if not _same_property_value(read_back, value):
        raise errors.SettingsError(
            f"Resolve accepted but did not persist setting {key!r}.",
            cause=f"Wrote {value!r}, read back {read_back!r}.",
            fix="The value may be invalid for this Resolve build. See `dvr schema settings`.",
            state={"key": key, "wrote": value, "read_back": read_back},
        )


def _apply_mutations(
    spec: Spec,
    resolve: Resolve,
    *,
    continue_on_error: bool,
    verify: bool,
) -> None:
    """Run every mutation in ``spec`` against ``resolve`` (extracted so
    :func:`apply` can wrap the whole batch in snapshot/rollback)."""
    applied: list[Action] = []
    failures: list[dict[str, Any]] = []

    def record_failure(action: Action, exc: errors.DvrError) -> None:
        failures.append(
            {
                "op": action.op,
                "target": action.target,
                "detail": action.detail,
                "payload": action.payload,
                "error": exc.to_dict(),
            }
        )
        applied.append(
            Action(
                op="error",
                target=action.target,
                detail=exc.message,
                payload={"action": action.payload, "error": exc.to_dict()},
            )
        )

    def apply_or_record(action: Action, fn: Callable[[], None]) -> None:
        try:
            fn()
            applied.append(action)
        except errors.DvrError as exc:
            if not continue_on_error:
                raise
            record_failure(action, exc)

    # Project — get-or-create.
    project = resolve.project.ensure(spec.project)

    # Setting writes: plain, or write + read-back verification.
    set_setting: Callable[[Any, str, Any], None] = (
        _verified_set_setting
        if verify
        else (lambda target, key, value: target.set_setting(key, value))
    )

    # Project-level settings, ordered so HDR setup works.
    desired: dict[str, str] = {}
    if spec.color_preset:
        desired.update(COLOR_PRESETS[spec.color_preset])
    desired.update(spec.settings)

    for key in SETTINGS_ORDER:
        if key in desired:
            value = desired[key]
            apply_or_record(
                Action(
                    op="set",
                    target=f"project:{spec.project}/setting:{key}",
                    detail=f"= {value}",
                    payload={"key": key, "value": value},
                ),
                partial(set_setting, project, key, value),
            )
    for key, value in desired.items():
        if key not in SETTINGS_ORDER:
            apply_or_record(
                Action(
                    op="set",
                    target=f"project:{spec.project}/setting:{key}",
                    detail=f"= {value}",
                    payload={"key": key, "value": value},
                ),
                partial(set_setting, project, key, value),
            )

    # Bins (nested paths, idempotent).
    for bin_path in spec.bins:

        def ensure_bin(path: str = bin_path) -> None:
            project.media.ensure_folder_path(path)

        apply_or_record(
            Action(op="ensure", target=f"bin:{bin_path}", payload={"path": bin_path}),
            ensure_bin,
        )

    # Timelines.
    for tl_spec in spec.timelines:
        tl = project.timeline.ensure(tl_spec.name)
        for track_type, count in sorted(tl_spec.tracks.items()):

            def ensure_tracks(timeline: Any = tl, tt: str = track_type, n: int = count) -> None:
                while timeline.track_count(tt) < n:
                    timeline.add_track(tt)

            apply_or_record(
                Action(
                    op="ensure",
                    target=f"timeline:{tl_spec.name}/tracks:{track_type}",
                    detail=f">= {count}",
                    payload={"timeline": tl_spec.name, "track_type": track_type, "count": count},
                ),
                ensure_tracks,
            )
        for key, value in tl_spec.settings.items():
            apply_or_record(
                Action(
                    op="set",
                    target=f"timeline:{tl_spec.name}/setting:{key}",
                    detail=f"= {value}",
                    payload={"key": key, "value": value, "timeline": tl_spec.name},
                ),
                partial(set_setting, tl, key, value),
            )
        if tl_spec.fps is not None:
            apply_or_record(
                Action(
                    op="set",
                    target=f"timeline:{tl_spec.name}/setting:timelineFrameRate",
                    detail=f"= {tl_spec.fps}",
                    payload={
                        "key": "timelineFrameRate",
                        "value": str(tl_spec.fps),
                        "timeline": tl_spec.name,
                    },
                ),
                partial(set_setting, tl, "timelineFrameRate", str(tl_spec.fps)),
            )
        existing_markers = tl.markers()
        for marker in tl_spec.markers:
            frame = int(marker.get("frame", 0))
            if frame not in existing_markers:
                apply_or_record(
                    Action(
                        op="set",
                        target=f"timeline:{tl_spec.name}/marker:{frame}",
                        detail=str(marker.get("name", "")),
                        payload={"timeline": tl_spec.name, "marker": marker},
                    ),
                    partial(
                        tl.add_marker,
                        frame=frame,
                        color=str(marker.get("color", "Blue")),
                        name=str(marker.get("name", "")),
                        note=str(marker.get("note", "")),
                        duration=int(marker.get("duration", 1)),
                        custom_data=str(marker.get("custom_data", "")),
                    ),
                )
        for operation in tl_spec.clip_properties:
            action = Action(
                op="set",
                target=f"timeline:{tl_spec.name}/clip-properties:{_selector_label(operation.selector)}",
                detail=", ".join(f"{k}={v}" for k, v in operation.properties.items()),
                payload={
                    "timeline": tl_spec.name,
                    "selector": operation.selector,
                    "properties": operation.properties,
                },
            )

            def apply_clip_properties(
                timeline: Any = tl,
                op: ClipOperationSpec = operation,
            ) -> None:
                items = _select_timeline_items(timeline, op.selector)
                if not items:
                    raise errors.ClipError(
                        "Clip property selector matched no timeline items.",
                        fix="Check selector fields against `dvr clip ls` or `timeline.inspect()`.",
                        state={"timeline": timeline.name, "selector": op.selector},
                    )
                for item in items:
                    for key, value in op.properties.items():
                        try:
                            current = item.get_property(key)
                        except Exception:
                            current = None
                        if _same_property_value(current, value):
                            continue
                        item.set_property(key, value)

            apply_or_record(action, apply_clip_properties)

        for title_spec in tl_spec.titles:
            title_action = Action(
                op="ensure",
                target=f"timeline:{tl_spec.name}/title:{title_spec.text}",
                detail=", ".join(f"{k}={v}" for k, v in sorted(title_spec.styling.items())),
                payload={
                    "timeline": tl_spec.name,
                    "text": title_spec.text,
                    "title": title_spec.title,
                    "at": title_spec.at,
                    "styling": title_spec.styling,
                },
            )

            def apply_title(timeline: Any = tl, ts: TitleSpec = title_spec) -> None:
                existing = _find_text_item(timeline, ts.text)
                if existing is not None:
                    existing.text.set(text=ts.text, **ts.styling)
                    return
                if ts.at is not None:
                    timeline.current_timecode = ts.at
                timeline.insert_title(ts.title, fusion=ts.fusion, text=ts.text, **ts.styling)

            apply_or_record(title_action, apply_title)

    if failures:
        raise errors.SpecError(
            f"Spec applied with {len(failures)} failed action(s).",
            cause="One or more setting/marker operations failed while continue_on_error=True.",
            fix="Inspect state.failures, fix the invalid keys or values, and re-run the same spec.",
            state={"project": spec.project, "failures": failures},
        )


# ---------------------------------------------------------------------------
# Export (adopt an existing project into a spec — "terraform import")
# ---------------------------------------------------------------------------

# Project settings worth round-tripping through a spec / snapshot. The full
# GetSetting() set returns >200 keys, most of which are UI prefs.
CAPTURED_SETTINGS: tuple[str, ...] = (
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


def from_live(resolve: Resolve, *, project: str | None = None) -> dict[str, Any]:
    """Build a spec dict from live project state.

    The inverse of :func:`apply` — adopt an existing project into a spec
    file so future changes go through ``dvr plan`` / ``dvr apply``::

        data = spec.from_live(r)
        Path("show.yaml").write_text(yaml.safe_dump(data, sort_keys=False))

    Captures the color/format settings subset (:data:`CAPTURED_SETTINGS`),
    the bin tree, and each timeline's fps, track counts, and markers.
    Grades, Fusion comps, and clip contents are not representable in a
    spec and are omitted.
    """
    if project is not None:
        proj = resolve.project.ensure(project)
    else:
        proj = resolve.project.require_current()

    settings: dict[str, str] = {}
    for key in CAPTURED_SETTINGS:
        try:
            value = proj.get_setting(key)
        except Exception:  # boundary
            continue
        if value not in (None, ""):
            settings[key] = str(value)

    bins: list[str] = []
    with contextlib.suppress(Exception):  # boundary: media pool unavailable
        _collect_bin_paths(proj.media.root, "", bins)

    timelines: list[dict[str, Any]] = []
    for tl in proj.timeline.list():
        entry: dict[str, Any] = {"name": tl.name}
        with contextlib.suppress(Exception):  # boundary
            entry["fps"] = tl.fps
        tracks: dict[str, int] = {}
        for track_type in ("video", "audio", "subtitle"):
            try:
                count = int(tl.track_count(track_type))
            except Exception:  # boundary
                continue
            if count:
                tracks[track_type] = count
        if tracks:
            entry["tracks"] = tracks
        markers = []
        for frame, info in sorted((tl.markers() or {}).items()):
            markers.append(
                {
                    "frame": int(frame),
                    "color": str(info.get("color", "Blue")),
                    "name": str(info.get("name", "")),
                }
            )
        if markers:
            entry["markers"] = markers
        timelines.append(entry)

    out: dict[str, Any] = {"project": proj.name}
    if settings:
        out["settings"] = settings
    if bins:
        out["bins"] = bins
    if timelines:
        out["timelines"] = timelines
    return out


def _collect_bin_paths(folder: Any, prefix: str, out: list[str]) -> None:
    for sub in folder.subfolders:
        path = f"{prefix}/{sub.name}" if prefix else str(sub.name)
        out.append(path)
        _collect_bin_paths(sub, path, out)


__all__ = [
    "CAPTURED_SETTINGS",
    "COLOR_PRESETS",
    "Action",
    "ClipOperationSpec",
    "Hook",
    "Spec",
    "TimelineSpec",
    "TitleSpec",
    "apply",
    "from_live",
    "load_spec",
    "parse_spec",
    "plan",
]
