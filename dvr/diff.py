"""Structured diff between Resolve states.

The Resolve UI has no compare tool. We get one for free off ``inspect()``
snapshots: every domain object exposes a JSON-friendly snapshot, and a
tree-walk against any two of them produces a structured changeset.

Public API::

    from dvr import diff
    diff.compare(left, right) -> Diff
    diff.compare_timelines(tl_a, tl_b) -> Diff
    diff.compare_to_spec(resolve, spec) -> Diff

The :class:`Diff` is JSON-serializable; the CLI renders it as either a
diff-friendly table or as machine-readable JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .resolve import Resolve
    from .spec import Spec
    from .timeline import Timeline


# ---------------------------------------------------------------------------
# Diff data structures
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """A single difference between two states."""

    op: str  # "added" | "removed" | "changed"
    path: str  # dotted path, e.g. "tracks.video[1].clips[2].name"
    left: Any = None
    right: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Diff:
    """A complete diff between two states."""

    changes: list[Change] = field(default_factory=list)
    left_label: str = "left"
    right_label: str = "right"

    @property
    def empty(self) -> bool:
        return not self.changes

    def added(self) -> list[Change]:
        return [c for c in self.changes if c.op == "added"]

    def removed(self) -> list[Change]:
        return [c for c in self.changes if c.op == "removed"]

    def changed(self) -> list[Change]:
        return [c for c in self.changes if c.op == "changed"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left_label,
            "right": self.right_label,
            "summary": {
                "added": len(self.added()),
                "removed": len(self.removed()),
                "changed": len(self.changed()),
                "total": len(self.changes),
            },
            "changes": [c.to_dict() for c in self.changes],
        }


# ---------------------------------------------------------------------------
# Tree walk
# ---------------------------------------------------------------------------


def _walk(left: Any, right: Any, path: str, out: list[Change]) -> None:
    """Recursively diff two JSON-friendly values into ``out``."""
    if left == right:
        return
    if type(left) is not type(right):
        out.append(Change(op="changed", path=path, left=left, right=right))
        return
    if isinstance(left, dict):
        left_keys = set(left.keys())
        right_keys = set(right.keys())
        for k in sorted(left_keys - right_keys):
            out.append(Change(op="removed", path=_join(path, k), left=left[k]))
        for k in sorted(right_keys - left_keys):
            out.append(Change(op="added", path=_join(path, k), right=right[k]))
        for k in sorted(left_keys & right_keys):
            _walk(left[k], right[k], _join(path, k), out)
        return
    if isinstance(left, list):
        # For lists of dicts with a "name" or "id" key, align by that
        # identifier; otherwise compare positionally. Aligning by name
        # avoids spurious "everything changed" noise when ordering shifts.
        key = _list_key(left, right)
        if key is not None:
            _diff_keyed_list(left, right, key, path, out)
        else:
            _diff_positional_list(left, right, path, out)
        return
    out.append(Change(op="changed", path=path, left=left, right=right))


def _list_key(left: list[Any], right: list[Any]) -> str | None:
    candidates = ("name", "id", "shot_id", "frame", "index")
    if not left and not right:
        return None
    samples = (left[:1] + right[:1])[:2]
    for candidate in candidates:
        if all(isinstance(s, dict) and candidate in s for s in samples):
            return candidate
    return None


def _diff_keyed_list(
    left: list[Any], right: list[Any], key: str, path: str, out: list[Change]
) -> None:
    left_map = {item[key]: item for item in left}
    right_map = {item[key]: item for item in right}
    for k in sorted(left_map.keys() - right_map.keys(), key=str):
        out.append(Change(op="removed", path=_join(path, f"[{key}={k}]"), left=left_map[k]))
    for k in sorted(right_map.keys() - left_map.keys(), key=str):
        out.append(Change(op="added", path=_join(path, f"[{key}={k}]"), right=right_map[k]))
    for k in sorted(left_map.keys() & right_map.keys(), key=str):
        _walk(left_map[k], right_map[k], _join(path, f"[{key}={k}]"), out)


def _diff_positional_list(left: list[Any], right: list[Any], path: str, out: list[Change]) -> None:
    for i, (a, b) in enumerate(zip(left, right, strict=False)):
        _walk(a, b, _join(path, f"[{i}]"), out)
    for i in range(len(left), len(right)):
        out.append(Change(op="added", path=_join(path, f"[{i}]"), right=right[i]))
    for i in range(len(right), len(left)):
        out.append(Change(op="removed", path=_join(path, f"[{i}]"), left=left[i]))


def _join(prefix: str, segment: str) -> str:
    if not prefix:
        return segment
    if segment.startswith("["):
        return f"{prefix}{segment}"
    return f"{prefix}.{segment}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare(
    left: Any,
    right: Any,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> Diff:
    """Compare two arbitrary JSON-friendly values."""
    out: list[Change] = []
    left_dict = _to_inspectable(left)
    right_dict = _to_inspectable(right)
    _walk(left_dict, right_dict, "", out)
    return Diff(changes=out, left_label=left_label, right_label=right_label)


def compare_timelines(left: Timeline, right: Timeline) -> Diff:
    """Compare two timelines."""
    return compare(
        left.inspect(),
        right.inspect(),
        left_label=f"timeline:{left.name}",
        right_label=f"timeline:{right.name}",
    )


def compare_to_spec(resolve: Resolve, spec: Spec) -> Diff:
    """Compare the live Resolve state against a desired spec."""
    from .spec import COLOR_PRESETS

    live = _live_snapshot(resolve, spec)
    desired = _spec_snapshot(spec, COLOR_PRESETS)
    return compare(live, desired, left_label="live", right_label=f"spec:{spec.project}")


def _live_snapshot(resolve: Resolve, spec: Spec) -> dict[str, Any]:
    """Return the subset of live state that ``spec`` claims to control."""
    project = resolve.project.current
    if project is None or project.name != spec.project:
        for name in resolve.project.list():
            if name == spec.project:
                project = resolve.project.load(name)
                break
    if project is None:
        return {"project": None}
    snapshot: dict[str, Any] = {"project": project.name}
    keys = set(spec.settings.keys())
    keys.update(_preset_keys_for(spec.color_preset))
    if keys:
        snapshot["settings"] = {k: project.get_setting(k) for k in sorted(keys)}
    timelines: list[dict[str, Any]] = []
    for tl_spec in spec.timelines:
        try:
            tl = project.timeline.get(tl_spec.name)
        except Exception:
            timelines.append({"name": tl_spec.name, "exists": False})
            continue
        info: dict[str, Any] = {"name": tl_spec.name, "exists": True}
        if tl_spec.fps is not None:
            info["fps"] = tl.fps
        if tl_spec.settings:
            info["settings"] = {k: tl.get_setting(k) for k in sorted(tl_spec.settings)}
        if tl_spec.markers:
            existing = tl.markers()
            info["markers"] = [
                {
                    "frame": frame,
                    "color": data.get("color"),
                    "name": data.get("name"),
                }
                for frame, data in sorted(existing.items())
            ]
        timelines.append(info)
    snapshot["timelines"] = timelines
    return snapshot


def _spec_snapshot(spec: Spec, color_presets: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Return the desired-state snapshot from a spec."""
    desired: dict[str, Any] = {"project": spec.project}
    settings: dict[str, str] = {}
    if spec.color_preset:
        settings.update(color_presets[spec.color_preset])
    settings.update(spec.settings)
    if settings:
        desired["settings"] = dict(sorted(settings.items()))
    timelines: list[dict[str, Any]] = []
    for tl_spec in spec.timelines:
        info: dict[str, Any] = {"name": tl_spec.name, "exists": True}
        if tl_spec.fps is not None:
            info["fps"] = tl_spec.fps
        if tl_spec.settings:
            info["settings"] = dict(sorted(tl_spec.settings.items()))
        if tl_spec.markers:
            info["markers"] = [
                {
                    "frame": int(m.get("frame", 0)),
                    "color": m.get("color"),
                    "name": m.get("name"),
                }
                for m in tl_spec.markers
            ]
        timelines.append(info)
    desired["timelines"] = timelines
    return desired


def _preset_keys_for(preset_name: str | None) -> set[str]:
    if preset_name is None:
        return set()
    from .spec import COLOR_PRESETS

    return set(COLOR_PRESETS.get(preset_name, {}).keys())


def _to_inspectable(value: Any) -> Any:
    """Reduce wrapped objects to their inspect() snapshot."""
    if hasattr(value, "inspect") and callable(value.inspect):
        return value.inspect()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    return value


__all__ = ["Change", "Diff", "compare", "compare_timelines", "compare_to_spec"]
