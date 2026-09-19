"""Color page wrappers: grades, CDL, LUTs, node graphs, color groups.

Resolve's color API is read-mostly: you can inspect the node graph and
apply CDL/LUT operations, but you can't programmatically build node
trees. This module wraps what *is* exposed:

* :class:`NodeGraph` — read-only inspection of a clip's color nodes plus
  per-node enable/cache/LUT operations.
* :class:`ColorGroup` — Resolve's grouping mechanism for shared grades.
* :class:`ColorOps` — convenience methods on a :class:`Clip` for CDL,
  LUT export, magic mask, stabilization, smart reframe, and grade copy.

The :class:`ColorOps` accessor is exposed on :class:`Clip.color`.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, List  # noqa: UP035

from . import errors

if TYPE_CHECKING:
    from .timeline import TimelineItem

logger = logging.getLogger("dvr.color")


# ---------------------------------------------------------------------------
# NodeGraph
# ---------------------------------------------------------------------------


class NodeGraph:
    """A clip's color node graph (one layer)."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def num_nodes(self) -> int:
        return int(self._raw.GetNumNodes())

    def label(self, node_index: int) -> str:
        return str(self._raw.GetNodeLabel(node_index) or "")

    def set_enabled(self, node_index: int, enabled: bool) -> None:
        if not self._raw.SetNodeEnabled(node_index, bool(enabled)):
            raise errors.ColorError(
                f"Could not toggle node {node_index} enabled={enabled}.",
                state={"node_index": node_index, "enabled": enabled},
            )

    def tools(self, node_index: int) -> List[str]:  # noqa: UP006
        return [str(t) for t in (self._raw.GetToolsInNode(node_index) or [])]

    def set_lut(self, node_index: int, lut_path: str) -> None:
        if not self._raw.SetLUT(node_index, lut_path):
            raise errors.ColorError(
                f"Could not set LUT on node {node_index} to {lut_path!r}.",
                state={"node_index": node_index, "lut_path": lut_path},
            )

    def get_lut(self, node_index: int) -> str:
        return str(self._raw.GetLUT(node_index) or "")

    def reset_all(self) -> None:
        self._raw.ResetAllGrades()

    def apply_drx(self, drx_path: str, *, gradeMode: int = 0) -> None:
        if not self._raw.ApplyGradeFromDRX(drx_path, gradeMode):
            raise errors.ColorError(
                f"Could not apply grade from {drx_path!r}.",
                cause="ApplyGradeFromDRX returned False — file may be missing or invalid.",
                state={"drx_path": drx_path},
            )

    def apply_arri_cdl_lut(self) -> None:
        if not self._raw.ApplyArriCdlLut():
            raise errors.ColorError(
                "Could not apply ARRI CDL LUT.",
                cause="The clip may not have ARRI metadata.",
            )

    def inspect(self) -> dict[str, Any]:
        nodes = []
        for i in range(1, self.num_nodes + 1):
            nodes.append(
                {
                    "index": i,
                    "label": self.label(i),
                    "tools": self.tools(i),
                    "lut": self.get_lut(i),
                }
            )
        return {"num_nodes": self.num_nodes, "nodes": nodes}


# ---------------------------------------------------------------------------
# ColorGroup
# ---------------------------------------------------------------------------


class ColorGroup:
    """A color group (shared pre/post-clip grades for many clips)."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def name(self) -> str:
        return str(self._raw.GetName())

    @name.setter
    def name(self, value: str) -> None:
        if not self._raw.SetName(value):
            raise errors.ColorError(
                f"Could not rename color group to {value!r}.",
                state={"current": self.name, "requested": value},
            )

    def clips_in_timeline(self, timeline: Any) -> list[Any]:
        return list(self._raw.GetClipsInTimeline(timeline) or [])

    def pre_clip_graph(self) -> NodeGraph | None:
        raw = self._raw.GetPreClipNodeGraph()
        return NodeGraph(raw) if raw else None

    def post_clip_graph(self) -> NodeGraph | None:
        raw = self._raw.GetPostClipNodeGraph()
        return NodeGraph(raw) if raw else None

    def inspect(self) -> dict[str, Any]:
        return {"name": self.name}


# ---------------------------------------------------------------------------
# ColorOps — accessor exposed on Clip.color
# ---------------------------------------------------------------------------


# Resolve SetCDL accepts space-separated RGB triples, without a master channel.
CDLVector = tuple[float, float, float] | tuple[float, float, float, float]
CDLKeys = ("NodeIndex", "Slope", "Offset", "Power", "Saturation")


class ColorOps:
    """Color operations on a single :class:`Clip`."""

    def __init__(self, clip: TimelineItem) -> None:
        self._clip = clip
        self._raw = clip.raw

    # --- versions -------------------------------------------------------

    def add_version(self, name: str, *, version_type: int = 0) -> None:
        """Add a color version. ``version_type`` 0 = local, 1 = remote."""
        if not self._raw.AddVersion(name, version_type):
            raise errors.ColorError(
                f"Could not add version {name!r}.",
                state={"clip": self._clip.name, "name": name, "type": version_type},
            )

    def load_version(self, name: str, *, version_type: int = 0) -> None:
        if not self._raw.LoadVersionByName(name, version_type):
            raise errors.ColorError(
                f"Could not load version {name!r}.",
                cause="LoadVersionByName returned False — version may not exist.",
                state={"clip": self._clip.name, "name": name, "type": version_type},
            )

    def delete_version(self, name: str, *, version_type: int = 0) -> None:
        if not self._raw.DeleteVersionByName(name, version_type):
            raise errors.ColorError(
                f"Could not delete version {name!r}.",
                state={"clip": self._clip.name, "name": name, "type": version_type},
            )

    def rename_version(self, old: str, new: str, *, version_type: int = 0) -> None:
        if not self._raw.RenameVersionByName(old, new, version_type):
            raise errors.ColorError(
                f"Could not rename version {old!r} to {new!r}.",
                state={"clip": self._clip.name, "old": old, "new": new},
            )

    def versions(self, *, version_type: int = 0) -> List[str]:  # noqa: UP006
        return [str(v) for v in (self._raw.GetVersionNameList(version_type) or [])]

    def current_version(self) -> dict[str, Any]:
        return dict(self._raw.GetCurrentVersion() or {})

    # --- CDL / LUT -----------------------------------------------------

    def set_cdl(
        self,
        *,
        node_index: int = 1,
        slope: CDLVector | None = None,
        offset: CDLVector | None = None,
        power: CDLVector | None = None,
        saturation: float | None = None,
    ) -> None:
        """Apply a CDL grade to a single node.

        Each of ``slope``/``offset``/``power`` is an RGB triple. Omitted
        values are left untouched. Legacy four-tuples are accepted only when
        the fourth value is neutral (1 for slope/power, 0 for offset); Resolve
        has no CDL master channel.
        """
        if node_index < 1:
            raise errors.ColorError("CDL node index must be positive.")
        params: dict[str, Any] = {"NodeIndex": str(node_index)}
        for key, values, neutral in (
            ("Slope", slope, 1),
            ("Offset", offset, 0),
            ("Power", power, 1),
        ):
            if values is None:
                continue
            if len(values) not in (3, 4) or not all(math.isfinite(v) for v in values):
                raise errors.ColorError(f"CDL {key} requires three finite RGB values.")
            if len(values) == 4 and values[3] != neutral:
                raise errors.ColorError(
                    f"CDL {key} has no master channel.",
                    fix=f"Supply an RGB triple, or a legacy four-tuple with neutral master {neutral}.",
                )
            params[key] = " ".join(f"{v:.8g}" for v in values[:3])
        if saturation is not None:
            if not math.isfinite(saturation) or saturation < 0:
                raise errors.ColorError("CDL saturation must be finite and nonnegative.")
            params["Saturation"] = f"{saturation:.8g}"

        if not self._raw.SetCDL(params):
            raise errors.ColorError(
                f"Could not apply CDL on node {node_index}.",
                cause="SetCDL returned False.",
                state={"clip": self._clip.name, "params": params},
            )

    def export_lut(self, file_path: str, *, size: int | str = 33) -> None:
        """Export the current grade as a 1D/3D LUT.

        ``size`` corresponds to Resolve's ``EXPORT_LUT_*`` enum: 17, 33,
        65, or use ``"vlt"`` (Panasonic VLT). Defaults to 33-point cube.
        """
        try:
            enum_value = {17: 0, 33: 1, 65: 2, "vlt": 3}[size]
        except KeyError as exc:
            raise errors.ColorError(
                f"Unsupported LUT size {size!r}.",
                fix="Use 17, 33, 65, or 'vlt' (Panasonic).",
            ) from exc
        if not self._raw.ExportLUT(enum_value, file_path):
            raise errors.ColorError(
                f"Could not export LUT to {file_path!r}.",
                cause="ExportLUT returned False.",
                state={"clip": self._clip.name, "size": size, "file_path": file_path},
            )

    # --- grades / nodes ------------------------------------------------

    def graph(self, layer: int = 1) -> NodeGraph:
        raw = self._raw.GetNodeGraph(layer)
        if raw is None:
            raise errors.ColorError(
                f"No color node graph at layer {layer}.",
                cause="GetNodeGraph returned None.",
                state={"clip": self._clip.name, "layer": layer},
            )
        return NodeGraph(raw)

    def copy_grades_to(self, other_clips: list[TimelineItem]) -> None:
        raws = [c.raw for c in other_clips]
        if not self._raw.CopyGrades(raws):
            raise errors.ColorError(
                f"Could not copy grade to {len(raws)} clip(s).",
                state={"source_clip": self._clip.name, "target_count": len(raws)},
            )

    def reset_node_colors(self) -> None:
        self._raw.ResetAllNodeColors()

    # --- magic mask / stabilization / reframe --------------------------

    def magic_mask(self, mode: str = "BI") -> None:
        """Run Magic Mask tracking. ``mode`` is ``F``, ``B``, or ``BI``."""
        if mode not in ("F", "B", "BI"):
            raise errors.ColorError(
                f"Magic Mask mode must be one of F/B/BI, got {mode!r}.",
                state={"clip": self._clip.name, "mode": mode},
            )
        if not self._raw.CreateMagicMask(mode):
            raise errors.ColorError(
                "CreateMagicMask returned False.",
                cause="The clip may have no Magic Mask stroke drawn yet.",
                fix="Open the Color page and draw a stroke before tracking.",
                state={"clip": self._clip.name, "mode": mode},
            )

    def regenerate_magic_mask(self) -> None:
        if not self._raw.RegenerateMagicMask():
            raise errors.ColorError(
                "RegenerateMagicMask returned False.",
                state={"clip": self._clip.name},
            )

    def stabilize(self) -> None:
        if not self._raw.Stabilize():
            raise errors.ColorError(
                "Stabilize returned False.",
                state={"clip": self._clip.name},
            )

    def smart_reframe(self) -> None:
        if not self._raw.SmartReframe():
            raise errors.ColorError(
                "SmartReframe returned False.",
                state={"clip": self._clip.name},
            )

    # --- color groups --------------------------------------------------

    def color_group(self) -> ColorGroup | None:
        raw = self._raw.GetColorGroup()
        return ColorGroup(raw) if raw else None

    def assign_to(self, group: ColorGroup) -> None:
        if not self._raw.AssignToColorGroup(group.raw):
            raise errors.ColorError(
                f"Could not assign clip to color group {group.name!r}.",
                state={"clip": self._clip.name, "group": group.name},
            )

    def remove_from_group(self) -> None:
        self._raw.RemoveFromColorGroup()


__all__ = ["ColorGroup", "ColorOps", "NodeGraph"]
