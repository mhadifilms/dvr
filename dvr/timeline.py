"""Timeline, track, and timeline-item wrappers.

The Resolve API distinguishes between *MediaPoolItem* (a clip in the
bin — see :class:`dvr.media.Clip`) and *TimelineItem* (an instance of a
clip placed on a track — :class:`TimelineItem` here). This module
mirrors that, but flattens the navigation: you almost never need to call
``GetItemListInTrack`` yourself. ::

    tl = r.timeline.current
    for clip in tl.tracks.video[1].items:    # V2 timeline items
        if clip.duration < 24:
            clip.add_marker(color="Red")

A single :meth:`Timeline.inspect` call returns a structured snapshot of
the whole timeline — tracks, items, markers, settings — that is the
recommended way to read state. Mutations go through dedicated methods.

Naming notes
------------

The class on a timeline track is :class:`TimelineItem`. Older releases
called it ``Clip``; ``Clip`` now refers to media-pool items
(:class:`dvr.media.Clip`). The ``ClipQuery`` query object is renamed to
:class:`ItemQuery`; the older name remains as a deprecated alias.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, List  # noqa: UP035 — `List` avoids `list` method shadow

from . import errors
from ._wrap import require

if TYPE_CHECKING:
    from .color import ColorOps
    from .media import Clip as MediaClip

logger = logging.getLogger("dvr.timeline")


# ---------------------------------------------------------------------------
# Track helpers
# ---------------------------------------------------------------------------


_TRACK_TYPES = ("video", "audio", "subtitle")


def _validate_track_type(track_type: str) -> str:
    if track_type not in _TRACK_TYPES:
        raise errors.TrackError(
            f"Unknown track type {track_type!r}.",
            cause=f"Track type must be one of {_TRACK_TYPES}.",
            fix=f"Use one of {_TRACK_TYPES}.",
            state={"requested": track_type},
        )
    return track_type


# ---------------------------------------------------------------------------
# TimelineItem (an instance of a clip placed on a timeline)
# ---------------------------------------------------------------------------


class TimelineItem:
    """A clip placed on a timeline (Resolve's ``TimelineItem``).

    For the underlying source clip in the media pool, see
    :attr:`TimelineItem.clip` (or :class:`dvr.media.Clip` directly).
    """

    def __init__(self, raw: Any, *, track_type: str, track_index: int) -> None:
        self._raw = raw
        self._track_type = track_type
        self._track_index = track_index

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def name(self) -> str:
        return self._raw.GetName()

    @property
    def start(self) -> int:
        return self._raw.GetStart()

    @property
    def end(self) -> int:
        return self._raw.GetEnd()

    @property
    def duration(self) -> int:
        return self._raw.GetDuration()

    @property
    def track_type(self) -> str:
        return self._track_type

    @property
    def track_index(self) -> int:
        return self._track_index

    @property
    def enabled(self) -> bool:
        return bool(self._raw.GetClipEnabled())

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._raw.SetClipEnabled(bool(value))

    def get_property(self, key: str | None = None) -> Any:
        return self._raw.GetProperty(key) if key else self._raw.GetProperty()

    def set_property(self, key: str, value: Any, *, raise_on_failure: bool = True) -> bool:
        """Set a timeline-item property. Returns True on success.

        With ``raise_on_failure=True`` (default), raises :class:`ClipError`
        on failure. With ``raise_on_failure=False``, returns False so you
        can do batch counting like ``sum(1 for c in clips if c.set_property(...,
        raise_on_failure=False))``.
        """
        ok = bool(self._raw.SetProperty(key, value))
        if not ok and raise_on_failure:
            raise errors.ClipError(
                f"Could not set timeline-item property {key!r}.",
                cause="SetProperty returned False — invalid key, type, or value range.",
                fix="See `dvr schema clip-properties` for valid keys per Resolve version.",
                state={"item": self.name, "key": key, "value": value},
            )
        return ok

    def set_properties(
        self,
        properties: dict[str, Any] | None = None,
        *,
        raise_on_failure: bool = True,
        **kwargs: Any,
    ) -> dict[str, bool]:
        """Set multiple documented timeline-item properties.

        Friendly keys and enum names are normalized through
        :mod:`dvr.schema`, so ``{"crop_top": 120, "blend": "multiply"}``
        becomes Resolve's ``CropTop`` and ``CompositeMode`` integer
        constants before calling ``SetProperty``.
        """
        from . import schema

        requested = dict(properties or {})
        requested.update(kwargs)
        normalized = schema.normalize_clip_properties(requested)
        return {
            key: self.set_property(key, value, raise_on_failure=raise_on_failure)
            for key, value in normalized.items()
        }

    def reset_properties(
        self,
        groups: Iterable[str] | None = None,
        *,
        raise_on_failure: bool = True,
    ) -> dict[str, bool]:
        """Reset documented editing-property groups to DVR defaults."""
        from . import schema

        return self.set_properties(
            schema.reset_clip_properties(tuple(groups) if groups is not None else None),
            raise_on_failure=raise_on_failure,
        )

    @property
    def edit(self) -> ItemEdit:
        """Ergonomic editing helpers for transform/crop/composite properties."""
        return ItemEdit(self)

    def add_marker(
        self,
        *,
        frame: int | None = None,
        color: str = "Blue",
        name: str = "",
        note: str = "",
        duration: int = 1,
        custom_data: str = "",
    ) -> None:
        """Add a marker on this timeline item."""
        target = frame if frame is not None else self.start
        ok = self._raw.AddMarker(target, color, name, note, duration, custom_data)
        if not ok:
            raise errors.ClipError(
                f"Could not add marker on item {self.name!r}.",
                cause="AddMarker returned False — frame may be out of range, or color invalid.",
                state={
                    "item": self.name,
                    "frame": target,
                    "color": color,
                },
            )

    def replace(self, source_path: str, *, preserve_subclip: bool = True) -> None:
        """Relink to a new source file. Preserves grades / Fusion / tracking."""
        mp_item = self._raw.GetMediaPoolItem()
        if mp_item is None:
            raise errors.ClipError(
                f"Item {self.name!r} has no underlying MediaPoolItem.",
                cause="GetMediaPoolItem returned None — the item may be a generator or compound.",
                state={"item": self.name},
            )
        ok = (
            mp_item.ReplaceClipPreserveSubClip(source_path)
            if preserve_subclip and hasattr(mp_item, "ReplaceClipPreserveSubClip")
            else mp_item.ReplaceClip(source_path)
        )
        if not ok:
            raise errors.ClipError(
                f"Could not relink {self.name!r} to {source_path!r}.",
                cause="ReplaceClip returned False — the source path may be missing or invalid.",
                fix="Confirm the file exists and is readable.",
                state={"item": self.name, "source_path": source_path},
            )

    @property
    def source_range(self) -> tuple[int, int]:
        """Source-media frame range used by this item.

        Returns ``(start_frame, end_frame)`` — the source media's frame
        indices that this timeline item exposes, regardless of the item's
        position on the timeline. Useful for resolving sub-clips and
        cross-referencing against the source file.
        """
        return (int(self._raw.GetSourceStartFrame()), int(self._raw.GetSourceEndFrame()))

    @property
    def is_text(self) -> bool:
        """True iff this item carries a Fusion Text+ tool (a styleable title).

        Best-effort: returns False for normal clips and anything without an
        editable ``TextPlus`` node. Use :attr:`text` to read or style it.
        """
        try:
            comp = self.fusion.comp(1)
            return bool(comp is not None and comp.text_tools())
        except Exception:  # boundary
            return False

    @property
    def is_compound(self) -> bool:
        """True iff this item is a compound clip on the timeline.

        Replaces the heuristic ``item.GetMediaPoolItem() is None and
        item.GetProperty('Type') == 'Compound Clip'``.
        """
        if self._raw.GetMediaPoolItem() is not None:
            return False
        try:
            kind = self._raw.GetProperty("Type")
        except Exception:  # boundary
            return False
        return str(kind or "") == "Compound Clip"

    # --- accessors to other domains ------------------------------------

    @property
    def clip(self) -> MediaClip | None:
        """The underlying media-pool clip, if any.

        Returns ``None`` for generators and compound clips.
        """
        from .media import Clip as _MediaClip

        raw = self._raw.GetMediaPoolItem()
        return _MediaClip(raw) if raw is not None else None

    # Legacy alias (older name when this returned an ``Asset``).
    @property
    def asset(self) -> MediaClip | None:
        return self.clip

    @property
    def color(self) -> ColorOps:
        """Color-page operations on this item (CDL, LUT, versions, masks)."""
        from .color import ColorOps as _ColorOps

        return _ColorOps(self)

    @property
    def fusion(self) -> ItemFusion:
        """Fusion-comp operations on this item (add, import, export, switch)."""
        return ItemFusion(self)

    @property
    def text(self) -> ItemText:
        """Text+ content and styling on this item (Fusion titles / ``Text+``)."""
        return ItemText(self)

    @property
    def takes(self) -> Takes:
        """Take/variant operations on this item."""
        return Takes(self)

    # --- output cache --------------------------------------------------

    def set_color_cache(self, mode: str = "auto") -> None:
        """Set color page output caching: ``auto`` | ``on`` | ``off``."""
        try:
            value = {"auto": 0, "on": 1, "off": 2}[mode]
        except KeyError as exc:
            raise errors.ClipError(
                f"Color cache mode must be auto/on/off, got {mode!r}.",
            ) from exc
        self._raw.SetColorOutputCache(value)

    def set_fusion_cache(self, mode: str = "auto") -> None:
        try:
            value = {"auto": 0, "on": 1, "off": 2}[mode]
        except KeyError as exc:
            raise errors.ClipError(
                f"Fusion cache mode must be auto/on/off, got {mode!r}.",
            ) from exc
        self._raw.SetFusionOutputCache(value)

    # --- magic mask (for cli/track.py) --------------------------------

    def create_magic_mask(self, mode: str = "BI") -> None:
        """Run Magic Mask tracking. ``mode`` is ``F``, ``B``, or ``BI``."""
        from .color import ColorOps as _ColorOps

        _ColorOps(self).magic_mask(mode)

    # --- inspection ---------------------------------------------------

    def inspect(self) -> dict[str, Any]:
        try:
            props = dict(self._raw.GetProperty() or {})
        except Exception:
            props = {}
        try:
            fusion_comps = list(self._raw.GetFusionCompNameList() or [])
        except Exception:
            fusion_comps = []
        try:
            versions = list(self._raw.GetVersionNameList(0) or [])
        except Exception:
            versions = []
        return {
            "name": self.name,
            "track_type": self._track_type,
            "track_index": self._track_index,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "enabled": self.enabled,
            "is_compound": self.is_compound,
            "properties": props,
            "fusion_comps": fusion_comps,
            "color_versions": versions,
        }


class ItemEdit:
    """Ergonomic wrappers around documented ``TimelineItem.SetProperty`` keys."""

    def __init__(self, item: TimelineItem) -> None:
        self._item = item

    def set(self, properties: dict[str, Any] | None = None, **kwargs: Any) -> TimelineItem:
        self._item.set_properties(properties, **kwargs)
        return self._item

    def transform(
        self,
        *,
        pan: float | None = None,
        tilt: float | None = None,
        zoom: float | None = None,
        zoom_x: float | None = None,
        zoom_y: float | None = None,
        zoom_gang: bool | None = None,
        rotation: float | None = None,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
        pitch: float | None = None,
        yaw: float | None = None,
        flip_x: bool | None = None,
        flip_y: bool | None = None,
    ) -> TimelineItem:
        props: dict[str, Any] = {}
        if pan is not None:
            props["pan"] = pan
        if tilt is not None:
            props["tilt"] = tilt
        if zoom is not None:
            props["zoom"] = zoom
        if zoom_x is not None:
            props["zoom_x"] = zoom_x
        if zoom_y is not None:
            props["zoom_y"] = zoom_y
        if zoom_gang is not None:
            props["zoom_gang"] = zoom_gang
        if rotation is not None:
            props["rotation"] = rotation
        if anchor_x is not None:
            props["anchor_x"] = anchor_x
        if anchor_y is not None:
            props["anchor_y"] = anchor_y
        if pitch is not None:
            props["pitch"] = pitch
        if yaw is not None:
            props["yaw"] = yaw
        if flip_x is not None:
            props["flip_x"] = flip_x
        if flip_y is not None:
            props["flip_y"] = flip_y
        return self.set(props)

    def crop(
        self,
        *,
        left: float | None = None,
        right: float | None = None,
        top: float | None = None,
        bottom: float | None = None,
        softness: float | None = None,
        retain: bool | None = None,
    ) -> TimelineItem:
        props: dict[str, Any] = {}
        if left is not None:
            props["crop_left"] = left
        if right is not None:
            props["crop_right"] = right
        if top is not None:
            props["crop_top"] = top
        if bottom is not None:
            props["crop_bottom"] = bottom
        if softness is not None:
            props["crop_softness"] = softness
        if retain is not None:
            props["crop_retain"] = retain
        return self.set(props)

    def composite(
        self,
        *,
        opacity: float | None = None,
        mode: str | int | None = None,
        distortion: float | None = None,
    ) -> TimelineItem:
        props: dict[str, Any] = {}
        if opacity is not None:
            props["opacity"] = opacity
        if mode is not None:
            props["composite_mode"] = mode
        if distortion is not None:
            props["distortion"] = distortion
        return self.set(props)

    def retime(
        self,
        *,
        process: str | int | None = None,
        motion_estimation: str | int | None = None,
    ) -> TimelineItem:
        props: dict[str, Any] = {}
        if process is not None:
            props["retime_process"] = process
        if motion_estimation is not None:
            props["motion_estimation"] = motion_estimation
        return self.set(props)

    def scaling(
        self,
        *,
        mode: str | int | None = None,
        resize_filter: str | int | None = None,
    ) -> TimelineItem:
        props: dict[str, Any] = {}
        if mode is not None:
            props["scaling"] = mode
        if resize_filter is not None:
            props["resize_filter"] = resize_filter
        return self.set(props)

    def dynamic_zoom(self, *, ease: str | int | None = None) -> TimelineItem:
        return self.set({"dynamic_zoom_ease": ease} if ease is not None else {})

    def reset(self, *groups: str) -> TimelineItem:
        self._item.reset_properties(groups or None)
        return self._item


# Deprecated alias — within the ``dvr.timeline`` module, ``Clip`` historically
# meant the timeline item. We keep that alias here so
# ``from dvr.timeline import Clip`` keeps working. Note that the package-level
# ``dvr.Clip`` now refers to :class:`dvr.media.Clip` (the media-pool item) —
# users importing ``Clip`` from ``dvr`` directly should rename to
# :class:`TimelineItem`.
Clip = TimelineItem


# ---------------------------------------------------------------------------
# Fusion comps on a TimelineItem
# ---------------------------------------------------------------------------


class FusionTool:
    """A Fusion tool node inside a composition."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def id(self) -> str:
        return str(getattr(self._raw, "ID", "") or "")

    @property
    def name(self) -> str:
        return str(getattr(self._raw, "Name", "") or "")

    def set_input(self, key: str, value: Any, *, frame: int = 0) -> None:
        try:
            ok = self._raw.SetInput(key, value, frame)
        except TypeError:
            ok = self._raw.SetInput(key, value)
        if ok is False:
            raise errors.FusionError(
                f"Could not set Fusion input {key!r} on tool {self.name or self.id!r}.",
                cause="SetInput returned False.",
                state={"tool": self.name, "id": self.id, "key": key, "value": value},
            )

    def get_input(self, key: str, *, frame: int = 0) -> Any:
        try:
            return self._raw.GetInput(key, frame)
        except TypeError:
            return self._raw.GetInput(key)

    def set_point(self, key: str, x: float, y: float, *, frame: int = 0) -> None:
        """Set a Fusion point input (e.g. a tool's ``Center``).

        Fusion point controls are set with a coordinate pair, but the
        scripting bridge accepts that pair in a couple of shapes depending
        on platform/version. Try each known shape and only fail if every
        one is rejected.
        """
        last_error: Exception | None = None
        for value in ([float(x), float(y)], {1: float(x), 2: float(y)}):
            try:
                ok = self._raw.SetInput(key, value, frame)
            except TypeError:
                try:
                    ok = self._raw.SetInput(key, value)
                except Exception as exc:  # boundary
                    last_error = exc
                    continue
            except Exception as exc:  # boundary
                last_error = exc
                continue
            if ok is not False:
                return
        raise errors.FusionError(
            f"Could not set Fusion point input {key!r} on tool {self.name or self.id!r}.",
            cause="SetInput rejected every supported point shape."
            + (f" Last error: {last_error}" if last_error is not None else ""),
            state={"tool": self.name, "id": self.id, "key": key, "x": x, "y": y},
        )

    def connect_input(
        self, input_name: str, source: FusionTool, output_name: str = "Output"
    ) -> None:
        for args in (
            (input_name, source.raw, output_name),
            (input_name, source.raw),
            (input_name, source.raw, "MainOutput"),
        ):
            try:
                if self._raw.ConnectInput(*args):
                    return
            except TypeError:
                continue
            except Exception:
                continue
        raise errors.FusionError(
            f"Could not connect {source.name or source.id!r} to {self.name or self.id!r}.",
            cause="ConnectInput rejected all supported call shapes.",
            state={
                "target": self.name,
                "target_id": self.id,
                "input": input_name,
                "source": source.name,
                "source_id": source.id,
                "output": output_name,
            },
        )


class FusionComp:
    """A Fusion composition attached to a timeline item."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    def tools(self) -> dict[str, FusionTool]:
        return {
            str(name): FusionTool(tool)
            for name, tool in (self._raw.GetToolList(False) or {}).items()
        }

    def find_tool(self, name: str) -> FusionTool | None:
        try:
            tool = self._raw.FindTool(name)
        except Exception:
            tool = None
        return FusionTool(tool) if tool else None

    def text_tools(self) -> List[FusionTool]:  # noqa: UP006
        """Every Text+ (``TextPlus``) tool in this comp, in tool order.

        Used by :class:`ItemText` to locate the editable text node inside a
        Fusion title or the ``Text+`` generator.
        """
        try:
            raw_map = self._raw.GetToolList(False, _TEXT_TOOL_REGID) or {}
        except TypeError:
            raw_map = {}
        tools = [FusionTool(t) for t in raw_map.values()]
        if tools:
            return tools
        return [tool for tool in self.tools().values() if _is_text_tool(tool)]

    def require_tool(self, name: str) -> FusionTool:
        tool = self.find_tool(name)
        if tool is None:
            raise errors.FusionError(
                f"Fusion comp has no tool named {name!r}.",
                cause="FindTool returned None.",
                state={"name": name},
            )
        return tool

    def add_tool(self, tool_id: str, x: float = 1, y: float = 0) -> FusionTool:
        tool = self._raw.AddTool(tool_id, x, y)
        if tool is None:
            raise errors.FusionError(
                f"Could not add Fusion tool {tool_id!r}.",
                cause="AddTool returned None.",
                fix="Confirm the tool is loaded in Resolve/Fusion and use the scripting tool ID.",
                state={"tool_id": tool_id, "x": x, "y": y},
            )
        return FusionTool(tool)


class ItemFusion:
    """Per-timeline-item Fusion comp operations."""

    def __init__(self, item: TimelineItem) -> None:
        self._item = item
        self._raw = item.raw

    def names(self) -> List[str]:  # noqa: UP006
        return [str(n) for n in (self._raw.GetFusionCompNameList() or [])]

    def add(self) -> Any:
        comp = self._raw.AddFusionComp()
        if comp is None:
            raise errors.FusionError(
                f"Could not add a Fusion comp on item {self._item.name!r}.",
                cause="AddFusionComp returned None.",
                state={"item": self._item.name},
            )
        return comp

    def add_comp(self) -> FusionComp:
        """Add a Fusion comp and return a wrapped composition."""
        return FusionComp(self.add())

    def load(self, name: str) -> Any:
        comp = self._raw.LoadFusionCompByName(name)
        if comp is None:
            raise errors.FusionError(
                f"Could not load Fusion comp {name!r}.",
                cause="LoadFusionCompByName returned None.",
                state={"item": self._item.name, "name": name},
            )
        return comp

    def load_comp(self, name: str) -> FusionComp:
        """Load a named Fusion comp and return a wrapped composition."""
        return FusionComp(self.load(name))

    def comp(self, index: int = 1, *, create: bool = False) -> FusionComp | None:
        """Return a wrapped Fusion comp by 1-based index.

        If ``create`` is true and the requested comp does not exist, add one.
        """
        try:
            raw = self._raw.GetFusionCompByIndex(index)
        except Exception:
            raw = None
        if raw is None and create:
            raw = self.add()
        return FusionComp(raw) if raw is not None else None

    def require_comp(self, index: int = 1, *, create: bool = False) -> FusionComp:
        comp = self.comp(index, create=create)
        if comp is None:
            raise errors.FusionError(
                f"Item {self._item.name!r} has no Fusion comp at index {index}.",
                cause="GetFusionCompByIndex returned None.",
                state={"item": self._item.name, "index": index},
            )
        return comp

    def import_(self, file_path: str) -> Any:
        comp = self._raw.ImportFusionComp(file_path)
        if comp is None:
            raise errors.FusionError(
                f"Could not import Fusion comp from {file_path!r}.",
                cause="ImportFusionComp returned None.",
                state={"item": self._item.name, "file_path": file_path},
            )
        return comp

    def export(self, name: str, file_path: str) -> None:
        if not self._raw.ExportFusionComp(name, file_path):
            raise errors.FusionError(
                f"Could not export Fusion comp {name!r} to {file_path!r}.",
                state={"item": self._item.name, "name": name, "file_path": file_path},
            )

    def rename(self, old: str, new: str) -> None:
        if not self._raw.RenameFusionCompByName(old, new):
            raise errors.FusionError(
                f"Could not rename Fusion comp {old!r} to {new!r}.",
                state={"item": self._item.name, "old": old, "new": new},
            )

    def delete(self, name: str) -> None:
        if not self._raw.DeleteFusionCompByName(name):
            raise errors.FusionError(
                f"Could not delete Fusion comp {name!r}.",
                state={"item": self._item.name, "name": name},
            )


# Deprecated alias.
ClipFusion = ItemFusion


# ---------------------------------------------------------------------------
# Text+ (Fusion title) customization
# ---------------------------------------------------------------------------


# Fusion registry ID of the Text+ tool that backs every Fusion title.
_TEXT_TOOL_REGID = "TextPlus"

# Text+ uses anchor inputs of -1/0/1 for alignment.
_HORIZONTAL_ALIGN: dict[str, int] = {"left": -1, "center": 0, "centre": 0, "middle": 0, "right": 1}
_VERTICAL_ALIGN: dict[str, int] = {"top": 1, "center": 0, "centre": 0, "middle": 0, "bottom": -1}

_NAMED_COLORS: dict[str, tuple[float, float, float]] = {
    "white": (1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "cyan": (0.0, 1.0, 1.0),
    "magenta": (1.0, 0.0, 1.0),
    "orange": (1.0, 0.5, 0.0),
    "purple": (0.5, 0.0, 0.5),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
}


def _is_text_tool(tool: FusionTool) -> bool:
    if tool.id == _TEXT_TOOL_REGID:
        return True
    try:
        attrs = tool.raw.GetAttrs() or {}
    except Exception:  # boundary
        return False
    return str(attrs.get("TOOLS_RegID", "")) == _TEXT_TOOL_REGID


def _parse_color(value: Any) -> tuple[float, float, float, float | None]:
    """Normalize a color into ``(r, g, b, a)`` floats in 0..1 (alpha optional).

    Accepts a hex string (``"#ff8800"`` / ``"#ff8800cc"``), a CSS-ish name
    (``"white"``, ``"red"`` ...), or a 3/4 number sequence (floats in 0..1,
    or ints in 0..255).
    """
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _NAMED_COLORS:
            r, g, b = _NAMED_COLORS[key]
            return (r, g, b, None)
        digits = key.lstrip("#")
        if len(digits) in (6, 8) and all(c in "0123456789abcdef" for c in digits):
            r = int(digits[0:2], 16) / 255.0
            g = int(digits[2:4], 16) / 255.0
            b = int(digits[4:6], 16) / 255.0
            a = int(digits[6:8], 16) / 255.0 if len(digits) == 8 else None
            return (r, g, b, a)
        raise errors.FusionError(
            f"Unrecognized color {value!r}.",
            fix="Use a hex string like '#ff8800', a name like 'white', or an (r,g,b) sequence.",
            state={"value": value},
        )
    if isinstance(value, (tuple, list)):
        nums = [float(c) for c in value]
        if len(nums) not in (3, 4):
            raise errors.FusionError(
                f"Color sequence must have 3 or 4 components, got {len(nums)}.",
                state={"value": list(value)},
            )
        if any(n > 1.0 for n in nums):
            nums = [n / 255.0 for n in nums]
        if len(nums) == 3:
            return (nums[0], nums[1], nums[2], None)
        return (nums[0], nums[1], nums[2], nums[3])
    raise errors.FusionError(
        f"Unsupported color value {value!r}.",
        fix="Pass a hex string, a color name, or an (r,g,b[,a]) sequence.",
        state={"value": value},
    )


def _coerce_point(position: Any) -> tuple[float, float]:
    if isinstance(position, dict):
        x = position.get("x", position.get(1, position.get("1")))
        y = position.get("y", position.get(2, position.get("2")))
        if x is None or y is None:
            raise errors.FusionError(
                f"Invalid text position {position!r}.",
                fix="Pass {'x': 0.5, 'y': 0.5} or an (x, y) pair (0..1, center is 0.5,0.5).",
                state={"position": position},
            )
        return float(x), float(y)
    if isinstance(position, (tuple, list)) and len(position) >= 2:
        return float(position[0]), float(position[1])
    raise errors.FusionError(
        f"Invalid text position {position!r}.",
        fix="Pass an (x, y) pair with 0..1 normalized coordinates (center is 0.5, 0.5).",
        state={"position": position},
    )


def _align_value(value: Any, mapping: dict[str, int], label: str) -> int:
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise errors.FusionError(f"{label} must be a name or anchor int, not a bool.")
    if isinstance(value, (int, float)):
        return int(value)
    key = str(value).strip().lower()
    if key not in mapping:
        raise errors.FusionError(
            f"Unknown {label} {value!r}.",
            fix=f"Use one of {sorted(set(mapping))}, or an anchor int (-1, 0, 1).",
            state={label: value},
        )
    return mapping[key]


class ItemText:
    """Read and customize the Text+ content and styling on a timeline item.

    The item must carry a Fusion ``TextPlus`` tool — true for Fusion
    *titles* inserted via :meth:`Timeline.insert_title` (e.g. ``"Text+"``).
    One :meth:`set` call drives the string, font, style, size, color,
    opacity, letter/line spacing, position, and alignment::

        item = tl.insert_title("Text+")
        item.text.set("HELLO", font="Open Sans", size=0.12, color="#ffcc00")

    Reads come back through :attr:`value` and :meth:`properties`.
    """

    def __init__(self, item: TimelineItem) -> None:
        self._item = item

    def tool(self) -> FusionTool:
        """The first ``TextPlus`` tool on the item, or raise a clear error."""
        comp = self._item.fusion.comp(1)
        if comp is None:
            raise errors.FusionError(
                f"Item {self._item.name!r} has no Fusion composition.",
                cause="GetFusionCompByIndex(1) returned None.",
                fix="Insert a Fusion title first, e.g. tl.insert_title('Text+').",
                state={"item": self._item.name},
            )
        tools = comp.text_tools()
        if not tools:
            raise errors.FusionError(
                f"Item {self._item.name!r} has no Text+ tool to edit.",
                cause="No TextPlus tool was found in the item's Fusion comp.",
                fix="Insert a Fusion title (e.g. tl.insert_title('Text+')) or add a TextPlus tool.",
                state={"item": self._item.name},
            )
        return tools[0]

    @property
    def value(self) -> str:
        """The current text string (Text+ ``StyledText``)."""
        return str(self.tool().get_input("StyledText") or "")

    @value.setter
    def value(self, text: str) -> None:
        self.set(text)

    def get(self, key: str) -> Any:
        """Read a raw Text+ Fusion input by ID (e.g. ``"Size"``)."""
        return self.tool().get_input(key)

    def set(
        self,
        text: str | None = None,
        *,
        font: str | None = None,
        style: str | None = None,
        size: float | None = None,
        color: Any | None = None,
        opacity: float | None = None,
        tracking: float | None = None,
        line_spacing: float | None = None,
        position: Any | None = None,
        align: str | int | None = None,
        vertical_align: str | int | None = None,
    ) -> TimelineItem:
        """Customize the Text+ tool. Only the arguments you pass are changed.

        Args:
            text:           The displayed string (``StyledText``).
            font:           Font family, e.g. ``"Open Sans"``.
            style:          Font style, e.g. ``"Regular"``, ``"Bold"``, ``"Italic"``.
            size:           Relative size (Text+ ``Size``, typically ~0.05-0.2).
            color:          Fill color - hex (``"#ffcc00"``), name (``"white"``),
                            or an ``(r, g, b[, a])`` sequence.
            opacity:        Text alpha, 0..1 (Text+ ``Alpha1``).
            tracking:       Letter spacing (Text+ ``Tracking``).
            line_spacing:   Line spacing (Text+ ``LineSpacing``).
            position:       ``(x, y)`` layout center, 0..1 (center is 0.5, 0.5).
            align:          Horizontal anchor: ``left`` / ``center`` / ``right``.
            vertical_align: Vertical anchor: ``top`` / ``center`` / ``bottom``.

        Returns the underlying :class:`TimelineItem` for chaining.
        """
        tool = self.tool()
        simple = {
            "StyledText": text,
            "Font": font,
            "Style": style,
            "Size": size,
            "Tracking": tracking,
            "LineSpacing": line_spacing,
        }
        for key, val in simple.items():
            if val is not None:
                tool.set_input(key, val)
        if color is not None:
            r, g, b, a = _parse_color(color)
            tool.set_input("Red1", r)
            tool.set_input("Green1", g)
            tool.set_input("Blue1", b)
            if a is not None:
                tool.set_input("Alpha1", a)
        if opacity is not None:
            tool.set_input("Alpha1", float(opacity))
        if position is not None:
            x, y = _coerce_point(position)
            tool.set_point("Center", x, y)
        if align is not None:
            tool.set_input("HorizontalAnchor", _align_value(align, _HORIZONTAL_ALIGN, "align"))
        if vertical_align is not None:
            tool.set_input(
                "VerticalAnchor", _align_value(vertical_align, _VERTICAL_ALIGN, "vertical_align")
            )
        return self._item

    def properties(self) -> dict[str, Any]:
        """Snapshot the editable Text+ inputs for inspection."""
        tool = self.tool()
        return {
            "tool": tool.name,
            "text": tool.get_input("StyledText"),
            "font": tool.get_input("Font"),
            "style": tool.get_input("Style"),
            "size": tool.get_input("Size"),
            "color": [
                tool.get_input("Red1"),
                tool.get_input("Green1"),
                tool.get_input("Blue1"),
            ],
            "opacity": tool.get_input("Alpha1"),
            "tracking": tool.get_input("Tracking"),
            "line_spacing": tool.get_input("LineSpacing"),
            "center": tool.get_input("Center"),
        }


# ---------------------------------------------------------------------------
# Takes on a TimelineItem
# ---------------------------------------------------------------------------


class Takes:
    """Take / variant management on a single timeline item."""

    def __init__(self, item: TimelineItem) -> None:
        self._item = item
        self._raw = item.raw

    @property
    def count(self) -> int:
        return int(self._raw.GetTakesCount())

    @property
    def selected_index(self) -> int:
        return int(self._raw.GetSelectedTakeIndex())

    def add(
        self,
        clip: Any,
        *,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> None:
        from .media import Clip as _MediaClip

        raw_clip = clip.raw if isinstance(clip, _MediaClip) else clip
        ok = (
            self._raw.AddTake(raw_clip, start_frame, end_frame)
            if start_frame is not None and end_frame is not None
            else self._raw.AddTake(raw_clip)
        )
        if not ok:
            raise errors.ClipError(
                f"Could not add take to item {self._item.name!r}.",
                state={"item": self._item.name},
            )

    def select(self, index: int) -> None:
        if not self._raw.SelectTakeByIndex(index):
            raise errors.ClipError(
                f"Could not select take {index}.",
                state={"item": self._item.name, "index": index, "count": self.count},
            )

    def get(self, index: int) -> dict[str, Any]:
        return dict(self._raw.GetTakeByIndex(index) or {})

    def delete(self, index: int) -> None:
        if not self._raw.DeleteTakeByIndex(index):
            raise errors.ClipError(
                f"Could not delete take {index}.",
                state={"item": self._item.name, "index": index},
            )

    def finalize(self) -> None:
        self._raw.FinalizeTake()


# ---------------------------------------------------------------------------
# Track wrapper
# ---------------------------------------------------------------------------


class Track:
    """A single video/audio/subtitle track on a timeline."""

    def __init__(self, timeline: Timeline, track_type: str, index: int) -> None:
        self._timeline = timeline
        self._raw = timeline.raw
        self._track_type = _validate_track_type(track_type)
        self._index = index

    @property
    def type(self) -> str:
        return self._track_type

    @property
    def index(self) -> int:
        return self._index

    @property
    def name(self) -> str:
        return self._raw.GetTrackName(self._track_type, self._index)

    @name.setter
    def name(self, value: str) -> None:
        if not self._raw.SetTrackName(self._track_type, self._index, value):
            raise errors.TrackError(
                f"Could not rename {self._track_type} track {self._index}.",
                state={"track_type": self._track_type, "index": self._index, "value": value},
            )

    @property
    def enabled(self) -> bool:
        return bool(self._raw.GetIsTrackEnabled(self._track_type, self._index))

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._raw.SetTrackEnable(self._track_type, self._index, bool(value))

    @property
    def locked(self) -> bool:
        return bool(self._raw.GetIsTrackLocked(self._track_type, self._index))

    @locked.setter
    def locked(self, value: bool) -> None:
        self._raw.SetTrackLock(self._track_type, self._index, bool(value))

    @property
    def subtype(self) -> str | None:
        """Audio channel format (mono/stereo/5.1/7.1/adaptive). None for V/S."""
        if self._track_type != "audio":
            return None
        return self._raw.GetTrackSubType(self._index)

    # --- items on this track ----------------------------------------

    @property
    def items(self) -> list[TimelineItem]:
        """Timeline items placed on this track, ordered by start frame."""
        raw_items = self._raw.GetItemListInTrack(self._track_type, self._index) or []
        return [
            TimelineItem(it, track_type=self._track_type, track_index=self._index)
            for it in raw_items
        ]

    # Legacy method-form alias.
    def clips(self) -> list[TimelineItem]:
        return self.items

    def find(
        self,
        *,
        name: str | None = None,
        predicate: Callable[[TimelineItem], bool] | None = None,
    ) -> TimelineItem | None:
        """Return the first item on this track matching ``name`` or ``predicate``.

        Mutually exclusive: pass either ``name=`` (exact match on
        :attr:`TimelineItem.name`) or ``predicate=`` (callable returning bool).
        Returns ``None`` if nothing matches.
        """
        if (name is None) == (predicate is None):
            raise errors.TrackError(
                "Track.find requires exactly one of name= or predicate=.",
                fix="Pass either name='clip.mov' or predicate=lambda it: ...",
            )
        check = predicate if predicate is not None else (lambda it: it.name == name)
        for item in self.items:
            if check(item):
                return item
        return None

    def find_all(
        self,
        *,
        name: str | None = None,
        predicate: Callable[[TimelineItem], bool] | None = None,
    ) -> list[TimelineItem]:
        """Like :meth:`find` but returns every match (possibly empty)."""
        if (name is None) == (predicate is None):
            raise errors.TrackError(
                "Track.find_all requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda it: it.name == name)
        return [item for item in self.items if check(item)]

    def delete(self) -> None:
        """Delete this track from the timeline."""
        if not self._raw.DeleteTrack(self._track_type, self._index):
            raise errors.TrackError(
                f"Could not delete {self._track_type} track {self._index}.",
                cause="DeleteTrack returned False — track may be locked or out of range.",
                state={"track_type": self._track_type, "index": self._index},
            )

    def inspect(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self._track_type,
            "index": self._index,
            "name": self.name,
            "enabled": self.enabled,
            "locked": self.locked,
            "item_count": len(self.items),
        }
        if self._track_type == "audio":
            result["subtype"] = self.subtype
        return result


# ---------------------------------------------------------------------------
# TrackList — `tl.tracks.video` (typed iterable + .add() shortcut)
# ---------------------------------------------------------------------------


class TrackList:
    """Iterable of :class:`Track` of a single type, with an ``add()`` shortcut.

    Accessed via :attr:`Timeline.tracks` (e.g. ``tl.tracks.video``). Indexed
    0-based per Python convention, so ``tl.tracks.video[0]`` is V1.
    """

    def __init__(self, timeline: Timeline, track_type: str) -> None:
        self._timeline = timeline
        self._track_type = _validate_track_type(track_type)

    def _all(self) -> list[Track]:
        count = self._timeline.track_count(self._track_type)
        return [Track(self._timeline, self._track_type, i) for i in range(1, count + 1)]

    def __iter__(self) -> Iterator[Track]:
        return iter(self._all())

    def __len__(self) -> int:
        return self._timeline.track_count(self._track_type)

    def __getitem__(self, index: int) -> Track:
        tracks = self._all()
        try:
            return tracks[index]
        except IndexError as exc:
            raise errors.TrackError(
                f"{self._track_type} track index {index} out of range",
                state={
                    "track_type": self._track_type,
                    "requested": index,
                    "count": len(tracks),
                },
            ) from exc

    def __bool__(self) -> bool:
        return self.__len__() > 0

    def add(self, *, subtype: str | None = None) -> Track:
        """Append a new track of this type and return it."""
        return self._timeline.add_track(self._track_type, subtype=subtype)


# ---------------------------------------------------------------------------
# TrackCollection — `tl.tracks` (gives you .video / .audio / .subtitle)
# ---------------------------------------------------------------------------


class TrackCollection:
    """Named-attribute access to track groups on a timeline.

    Supports ``tl.tracks.video[0]``, ``tl.tracks.audio.add()``,
    ``for tr in tl.tracks: ...`` (flattens all types), and the legacy
    callable form ``tl.tracks("video")`` returning a list.
    """

    def __init__(self, timeline: Timeline) -> None:
        self._timeline = timeline

    @property
    def video(self) -> TrackList:
        return TrackList(self._timeline, "video")

    @property
    def audio(self) -> TrackList:
        return TrackList(self._timeline, "audio")

    @property
    def subtitle(self) -> TrackList:
        return TrackList(self._timeline, "subtitle")

    def __iter__(self) -> Iterator[Track]:
        for tt in _TRACK_TYPES:
            yield from TrackList(self._timeline, tt)

    def __call__(self, track_type: str | None = None) -> list[Track]:
        """Legacy method-form: ``tl.tracks()`` or ``tl.tracks("video")``."""
        if track_type is None:
            return list(iter(self))
        return list(TrackList(self._timeline, track_type))


# ---------------------------------------------------------------------------
# MarkerCollection — dict-like access at `tl.markers`
# ---------------------------------------------------------------------------


class MarkerCollection:
    """Dict-like marker access with ``add()`` / ``remove()`` shortcuts.

    Supports ``tl.markers[120]``, ``120 in tl.markers``,
    ``tl.markers.add(120, color="Red")``, and the legacy callable form
    ``tl.markers()`` returning a plain dict.
    """

    def __init__(self, timeline: Timeline) -> None:
        self._timeline = timeline

    def _all(self) -> dict[int, dict[str, Any]]:
        return dict(self._timeline.raw.GetMarkers() or {})

    def __iter__(self) -> Iterator[int]:
        return iter(self._all())

    def __len__(self) -> int:
        return len(self._all())

    def __getitem__(self, frame: int) -> dict[str, Any]:
        markers = self._all()
        if frame not in markers:
            raise KeyError(frame)
        return markers[frame]

    def __contains__(self, frame: object) -> bool:
        return frame in self._all()

    def keys(self) -> Iterable[int]:
        return self._all().keys()

    def values(self) -> Iterable[dict[str, Any]]:
        return self._all().values()

    def items(self) -> Iterable[tuple[int, dict[str, Any]]]:
        return self._all().items()

    def add(
        self,
        frame: int,
        *,
        color: str = "Blue",
        name: str = "",
        note: str = "",
        duration: int = 1,
        custom_data: str = "",
    ) -> None:
        """Add a marker at ``frame``."""
        ok = self._timeline.raw.AddMarker(frame, color, name, note, duration, custom_data)
        if not ok:
            raise errors.TimelineError(
                f"Could not add marker at frame {frame}.",
                cause="AddMarker returned False — frame may be outside the timeline.",
                state={"frame": frame, "duration_frames": self._timeline.duration_frames},
            )

    def remove(self, frame: int) -> None:
        """Remove the marker at ``frame``."""
        if not self._timeline.raw.DeleteMarkerAtFrame(frame):
            raise errors.TimelineError(
                f"Could not remove marker at frame {frame}.",
                state={"frame": frame},
            )

    def remove_color(self, color: str) -> None:
        """Remove every marker of the given color."""
        self._timeline.raw.DeleteMarkersByColor(color)

    def find(
        self,
        *,
        color: str | None = None,
        name: str | None = None,
        custom_data: str | None = None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Return ``[(frame, marker), ...]`` matching all given filters.

        Each filter is an exact match on the marker's ``color`` / ``name`` /
        ``customData`` field. Pass nothing to return everything (sorted by
        frame).
        """
        results: list[tuple[int, dict[str, Any]]] = []
        for frame, marker in sorted(self._all().items()):
            if color is not None and str(marker.get("color", "")) != color:
                continue
            if name is not None and str(marker.get("name", "")) != name:
                continue
            if custom_data is not None and str(marker.get("customData", "")) != custom_data:
                continue
            results.append((frame, marker))
        return results

    def where(
        self,
        predicate: Callable[[int, dict[str, Any]], bool],
    ) -> list[tuple[int, dict[str, Any]]]:
        """Return ``[(frame, marker), ...]`` for which ``predicate(frame, marker)`` is True."""
        return [
            (frame, marker)
            for frame, marker in sorted(self._all().items())
            if predicate(frame, marker)
        ]

    def __call__(self) -> dict[int, dict[str, Any]]:
        """Legacy method-form: ``tl.markers()`` returns a plain dict."""
        return self._all()


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class Timeline:
    """A single timeline within a project."""

    def __init__(self, raw: Any, project: Any) -> None:
        self._raw = raw
        self._project_raw = project

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def name(self) -> str:
        return self._raw.GetName()

    @name.setter
    def name(self, value: str) -> None:
        if not self._raw.SetName(value):
            raise errors.TimelineError(
                f"Could not rename timeline to {value!r}.",
                cause="A timeline with this name may already exist in this project.",
                state={"current": self.name, "requested": value},
            )

    @property
    def start_frame(self) -> int:
        return self._raw.GetStartFrame()

    @property
    def end_frame(self) -> int:
        return self._raw.GetEndFrame()

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def start_timecode(self) -> str:
        return self._raw.GetStartTimecode()

    @property
    def current_timecode(self) -> str:
        return self._raw.GetCurrentTimecode()

    @current_timecode.setter
    def current_timecode(self, value: str) -> None:
        if not self._raw.SetCurrentTimecode(value):
            raise errors.TimelineError(
                f"Could not seek to timecode {value!r}.",
                cause="SetCurrentTimecode returned False — value may be out of range.",
                state={"requested": value, "duration_frames": self.duration_frames},
            )

    @property
    def fps(self) -> float:
        return float(self._raw.GetSetting("timelineFrameRate") or 0.0)

    # --- tracks -----------------------------------------------------------

    def track_count(self, track_type: str) -> int:
        return self._raw.GetTrackCount(_validate_track_type(track_type))

    @property
    def tracks(self) -> TrackCollection:
        """Track collection — ``tl.tracks.video``, ``tl.tracks.audio.add()``, etc.

        Also callable for backward compatibility: ``tl.tracks()`` /
        ``tl.tracks("video")`` return a plain list of :class:`Track`.
        """
        return TrackCollection(self)

    def track(self, track_type: str, index: int) -> Track:
        """Get a single track by type and 1-based index (Resolve convention)."""
        _validate_track_type(track_type)
        count = self.track_count(track_type)
        if index < 1 or index > count:
            raise errors.TrackError(
                f"{track_type} track {index} does not exist.",
                cause=f"Timeline has {count} {track_type} track(s).",
                state={"track_type": track_type, "requested_index": index, "count": count},
            )
        return Track(self, track_type, index)

    def add_track(self, track_type: str, *, subtype: str | None = None) -> Track:
        validated = _validate_track_type(track_type)
        ok = self._raw.AddTrack(validated, subtype) if subtype else self._raw.AddTrack(validated)
        if not ok:
            raise errors.TrackError(
                f"Could not add {validated} track.",
                cause="AddTrack returned False.",
                state={"track_type": validated, "subtype": subtype},
            )
        new_index = self.track_count(validated)
        return Track(self, validated, new_index)

    def delete_track(self, track_type: str, index: int) -> None:
        """Delete a track by type and 1-based index."""
        validated = _validate_track_type(track_type)
        if not self._raw.DeleteTrack(validated, index):
            raise errors.TrackError(
                f"Could not delete {validated} track {index}.",
                cause="DeleteTrack returned False — track may be locked or out of range.",
                state={"track_type": validated, "index": index},
            )

    # --- items / clips ----------------------------------------------------

    def items(self, track_type: str | None = None) -> ItemQuery:
        """Return a query over timeline items on ``track_type`` (or all)."""
        all_items: list[TimelineItem] = []
        for track in self.tracks(track_type):
            all_items.extend(track.items)
        return ItemQuery(all_items)

    # Legacy alias.
    def clips(self, track_type: str | None = None) -> ItemQuery:
        return self.items(track_type)

    def find_clip(
        self,
        predicate: Callable[[TimelineItem], bool] | None = None,
        *,
        name: str | None = None,
        track_type: str | None = None,
    ) -> TimelineItem | None:
        """Return the first timeline item across all (or filtered) tracks.

        Either pass a ``predicate`` callable, or ``name=`` for an exact
        :attr:`TimelineItem.name` match. ``track_type`` (``video`` /
        ``audio`` / ``subtitle``) restricts the search.
        """
        if (name is None) == (predicate is None):
            raise errors.TimelineError(
                "Timeline.find_clip requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda it: it.name == name)
        for track in self.tracks(track_type):
            for item in track.items:
                if check(item):
                    return item
        return None

    def find_clips(
        self,
        predicate: Callable[[TimelineItem], bool] | None = None,
        *,
        name: str | None = None,
        track_type: str | None = None,
    ) -> list[TimelineItem]:
        """Like :meth:`find_clip` but returns every match (possibly empty)."""
        if (name is None) == (predicate is None):
            raise errors.TimelineError(
                "Timeline.find_clips requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda it: it.name == name)
        results: list[TimelineItem] = []
        for track in self.tracks(track_type):
            results.extend(item for item in track.items if check(item))
        return results

    def find_gaps(
        self,
        *,
        track_type: str = "video",
        track_index: int = 1,
    ) -> list[tuple[int, int]]:
        """Return ``[(start_frame, end_frame), ...]`` for every gap on a track.

        A gap is the empty stretch between adjacent items, plus any space
        before the first item (relative to :attr:`start_frame`) and after
        the last item (relative to :attr:`end_frame`).

        Useful for sanity checks like "did the V2 build leave shot windows
        empty where V1 has content?". Sorted by start.
        """
        track = self.track(track_type, track_index)
        items = sorted(track.items, key=lambda it: it.start)
        gaps: list[tuple[int, int]] = []
        cursor = self.start_frame
        for item in items:
            if item.start > cursor:
                gaps.append((cursor, item.start))
            cursor = max(cursor, item.end)
        if cursor < self.end_frame:
            gaps.append((cursor, self.end_frame))
        return gaps

    def duplicate(self, name: str | None = None) -> Timeline:
        """Duplicate this timeline; returns the new :class:`Timeline`.

        If ``name`` is omitted, Resolve assigns a default (typically
        ``"<original> 1"``). Resolve switches the current timeline to the
        new copy.
        """
        raw = (
            self._raw.DuplicateTimeline(name) if name is not None else self._raw.DuplicateTimeline()
        )
        if raw is None:
            raise errors.TimelineError(
                f"Could not duplicate timeline {self.name!r}" + (f" as {name!r}." if name else "."),
                cause="DuplicateTimeline returned None — name may already exist.",
                state={"source": self.name, "requested": name},
            )
        return Timeline(raw, self._project_raw)

    def delete(self, items: Iterable[TimelineItem], *, ripple: bool = False) -> None:
        """Batch-delete timeline items. Convenience for :meth:`delete_clips`."""
        self.delete_clips(items, ripple=ripple)

    def delete_clips(self, items: Iterable[TimelineItem], *, ripple: bool = False) -> None:
        """Batch-delete timeline items.

        Args:
            items:  An iterable of :class:`TimelineItem` objects.
            ripple: If True, close the gap left by deleted items.

        Raises :class:`TimelineError` if Resolve refuses the delete.
        """
        raws = [c.raw for c in items]
        if not raws:
            return
        if not self._raw.DeleteClips(raws, bool(ripple)):
            raise errors.TimelineError(
                f"Could not delete {len(raws)} item(s).",
                cause="DeleteClips returned False — items may be on a locked track.",
                state={"count": len(raws), "ripple": ripple},
            )

    def create_compound_from_clips(
        self,
        items: Iterable[TimelineItem],
        *,
        name: str,
        start_timecode: str | None = None,
    ) -> TimelineItem:
        """Group a contiguous run of timeline items into a compound clip."""
        item_list = list(items)
        if not item_list:
            raise errors.TimelineError(
                "create_compound_from_clips called with no items.",
                fix="Pass at least one TimelineItem in the iterable.",
            )
        raws = [c.raw for c in item_list]
        info: dict[str, Any] = {"name": name}
        if start_timecode is not None:
            info["startTimecode"] = start_timecode
        result = self._raw.CreateCompoundClip(raws, info)
        if result is None:
            raise errors.TimelineError(
                f"Could not create compound clip {name!r}.",
                cause=(
                    "CreateCompoundClip returned None — items may not be "
                    "contiguous on the same track, or the name may already "
                    "be in use."
                ),
                state={"name": name, "item_count": len(raws)},
            )
        return TimelineItem(
            result,
            track_type=item_list[0].track_type,
            track_index=item_list[0].track_index,
        )

    # --- titles / text ----------------------------------------------------

    def _locate_video_track_index(self, raw_item: Any) -> int:
        """Best-effort: find which video track a freshly inserted item landed on."""
        try:
            target_id = raw_item.GetUniqueId()
        except Exception:  # boundary
            target_id = None
        if target_id is not None:
            for ti in range(1, self.track_count("video") + 1):
                for it in self._raw.GetItemListInTrack("video", ti) or []:
                    try:
                        if it.GetUniqueId() == target_id:
                            return ti
                    except Exception:  # boundary
                        continue
        return 1

    def insert_title(
        self,
        title: str = "Text+",
        *,
        fusion: bool = True,
        text: str | None = None,
        font: str | None = None,
        style: str | None = None,
        size: float | None = None,
        color: Any | None = None,
        opacity: float | None = None,
        tracking: float | None = None,
        line_spacing: float | None = None,
        position: Any | None = None,
        align: str | int | None = None,
        vertical_align: str | int | None = None,
    ) -> TimelineItem:
        """Insert a title at the playhead and optionally style its text.

        With ``fusion=True`` (default) this inserts a Fusion title such as
        the built-in ``"Text+"`` — the kind whose text you can drive via
        :attr:`TimelineItem.text`; any of the styling arguments below are
        applied immediately. With ``fusion=False`` it inserts a standard
        (non-Fusion) title, which exposes no scriptable text (the styling
        arguments are then ignored).

        The title lands at the current frame on the current video track, so
        seek first (e.g. ``tl.current_timecode = "01:00:05:00"``) to place
        it. Returns the new :class:`TimelineItem`.
        """
        method_name = "InsertFusionTitleIntoTimeline" if fusion else "InsertTitleIntoTimeline"
        insert = getattr(self._raw, method_name, None)
        if not callable(insert):
            raise errors.TimelineError(
                f"This DaVinci Resolve build cannot insert titles via {method_name}().",
                cause=f"The timeline object does not expose {method_name}.",
                fix="Update DaVinci Resolve, or insert the title manually on the Edit page.",
                state={"timeline": self.name, "title": title, "fusion": fusion},
            )
        raw_item = insert(title)
        if raw_item is None:
            raise errors.TimelineError(
                f"Could not insert title {title!r} into timeline {self.name!r}.",
                cause=f"{method_name} returned None — the title name may be unknown to Resolve.",
                fix="Use the exact name from Resolve's Effects → Titles list (e.g. 'Text+').",
                state={"timeline": self.name, "title": title, "fusion": fusion},
            )
        item = TimelineItem(
            raw_item,
            track_type="video",
            track_index=self._locate_video_track_index(raw_item),
        )
        styling = (
            text,
            font,
            style,
            size,
            color,
            opacity,
            tracking,
            line_spacing,
            position,
            align,
            vertical_align,
        )
        if fusion and any(v is not None for v in styling):
            item.text.set(
                text,
                font=font,
                style=style,
                size=size,
                color=color,
                opacity=opacity,
                tracking=tracking,
                line_spacing=line_spacing,
                position=position,
                align=align,
                vertical_align=vertical_align,
            )
        return item

    # --- markers ----------------------------------------------------------

    @property
    def markers(self) -> MarkerCollection:
        """Marker collection — dict-like, with ``.add()`` / ``.remove()`` shortcuts.

        Also callable for backward compatibility: ``tl.markers()`` returns a
        plain dict ``{frame: {color, name, note, duration, customData}}``.
        """
        return MarkerCollection(self)

    def add_marker(
        self,
        frame: int,
        *,
        color: str = "Blue",
        name: str = "",
        note: str = "",
        duration: int = 1,
        custom_data: str = "",
    ) -> None:
        """Convenience for :meth:`MarkerCollection.add`."""
        self.markers.add(
            frame,
            color=color,
            name=name,
            note=note,
            duration=duration,
            custom_data=custom_data,
        )

    # --- settings ---------------------------------------------------------

    def get_setting(self, key: str | None = None) -> Any:
        return self._raw.GetSetting(key) if key else self._raw.GetSetting()

    def set_setting(self, key: str, value: Any) -> None:
        if not self._raw.SetSetting(key, str(value)):
            raise errors.SettingsError(
                f"Could not set timeline setting {key!r} to {value!r}.",
                cause="SetSetting returned False — invalid key or value for this Resolve version.",
                state={"key": key, "value": value, "current": self._raw.GetSetting(key)},
            )

    # --- subtitles --------------------------------------------------------

    def create_subtitles_from_audio(
        self,
        *,
        language: str = "auto",
        chars_per_line: int = 42,
        line_break_type: str = "Auto",
        preset: str | None = None,
    ) -> None:
        """Run Resolve's Whisper-based audio-to-subtitle generation."""
        params: dict[str, Any] = {
            "language": language,
            "charactersPerLine": chars_per_line,
            "lineBreakType": line_break_type,
        }
        if preset is not None:
            params["preset"] = preset
        if not self._raw.CreateSubtitlesFromAudio(params):
            raise errors.TimelineError(
                f"Could not create subtitles for timeline {self.name!r}.",
                cause=(
                    "CreateSubtitlesFromAudio returned False. "
                    "Confirm Whisper models are downloaded in Resolve and that "
                    "the timeline has audio on at least one track."
                ),
                fix="Check Resolve's Edit page → Subtitles dropdown for download status.",
                state={"timeline": self.name, "params": params},
            )

    def detect_scene_cuts(self) -> bool:
        """Run Resolve's automatic scene-cut detection on the timeline."""
        return bool(self._raw.DetectSceneCuts())

    # --- inspection -------------------------------------------------------

    def inspect(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fps": self.fps,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_frames": self.duration_frames,
            "start_timecode": self.start_timecode,
            "tracks": {t: [tr.inspect() for tr in self.tracks(t)] for t in _TRACK_TYPES},
            "marker_count": len(self.markers),
        }


# ---------------------------------------------------------------------------
# ItemQuery — fluent filter / map over timeline items
# ---------------------------------------------------------------------------


class ItemQuery:
    """A composable, lazy query over a sequence of :class:`TimelineItem`.

    ``where`` returns a new :class:`ItemQuery`; iteration / ``list()`` /
    ``apply`` materialize it.
    """

    def __init__(self, items: list[TimelineItem]) -> None:
        self._items = items

    def where(self, predicate: Callable[[TimelineItem], bool]) -> ItemQuery:
        return ItemQuery([c for c in self._items if predicate(c)])

    def __iter__(self) -> Iterator[TimelineItem]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def first(self) -> TimelineItem | None:
        return self._items[0] if self._items else None

    def list(self) -> list[TimelineItem]:
        return list(self._items)

    def apply(self, fn: Callable[[TimelineItem], None]) -> int:
        for item in self._items:
            fn(item)
        return len(self._items)

    def set_properties(self, properties: dict[str, Any] | None = None, **kwargs: Any) -> int:
        """Set documented timeline-item properties on every item in the query."""

        def _set(item: TimelineItem) -> None:
            item.set_properties(properties, **kwargs)

        return self.apply(_set)

    def reset_properties(self, groups: Iterable[str] | None = None) -> int:
        def _reset(item: TimelineItem) -> None:
            item.reset_properties(groups)

        return self.apply(_reset)

    def transform(self, **kwargs: Any) -> int:
        def _transform(item: TimelineItem) -> None:
            item.edit.transform(**kwargs)

        return self.apply(_transform)

    def crop(self, **kwargs: Any) -> int:
        def _crop(item: TimelineItem) -> None:
            item.edit.crop(**kwargs)

        return self.apply(_crop)

    def composite(self, **kwargs: Any) -> int:
        def _composite(item: TimelineItem) -> None:
            item.edit.composite(**kwargs)

        return self.apply(_composite)

    def retime(self, **kwargs: Any) -> int:
        def _retime(item: TimelineItem) -> None:
            item.edit.retime(**kwargs)

        return self.apply(_retime)

    def scaling(self, **kwargs: Any) -> int:
        def _scaling(item: TimelineItem) -> None:
            item.edit.scaling(**kwargs)

        return self.apply(_scaling)


# Deprecated alias.
ClipQuery = ItemQuery


# ---------------------------------------------------------------------------
# Namespace exposed at Resolve.timeline / Project.timeline
# ---------------------------------------------------------------------------


class TimelineNamespace:
    """Operations on the timelines of a project."""

    def __init__(self, parent: Any) -> None:
        from .project import Project
        from .resolve import Resolve

        if isinstance(parent, Resolve):
            current = parent.project.current
            if current is None:
                raise errors.ProjectError(
                    "No project is currently loaded.",
                    cause="Resolve.project.current is None.",
                    fix="Load or create a project: `resolve.project.ensure('MyShow')`",
                )
            self._project: Project = current
        elif isinstance(parent, Project):
            self._project = parent
        else:
            self._project = Project(parent, None)

        self._raw = self._project.raw

    # --- read -------------------------------------------------------------

    @property
    def current(self) -> Timeline | None:
        raw = self._raw.GetCurrentTimeline()
        return Timeline(raw, self._raw) if raw is not None else None

    def list(self) -> List[Timeline]:  # noqa: UP006
        count = self._raw.GetTimelineCount()
        return [
            Timeline(self._raw.GetTimelineByIndex(i), self._raw)
            for i in range(1, count + 1)
            if self._raw.GetTimelineByIndex(i) is not None
        ]

    def names(self) -> List[str]:  # noqa: UP006
        return [tl.name for tl in self.list()]

    def get(self, name: str) -> Timeline:
        for tl in self.list():
            if tl.name == name:
                return tl
        raise errors.TimelineNotFoundError(
            f"No timeline named {name!r} in project {self._project.name!r}.",
            fix="Check available timelines via `resolve.timeline.names()`.",
            state={"requested": name, "available": self.names()},
        )

    # --- iteration -------------------------------------------------------

    def __iter__(self) -> Iterator[Timeline]:
        return iter(self.list())

    def __len__(self) -> int:
        return self._raw.GetTimelineCount()

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.names()

    def __getitem__(self, index_or_name: int | str) -> Timeline:
        if isinstance(index_or_name, str):
            return self.get(index_or_name)
        return self.list()[index_or_name]

    def by_name(self, name: str) -> Timeline:
        return self.get(name)

    # --- mutate -----------------------------------------------------------

    def create(self, name: str) -> Timeline:
        media_pool = require(
            self._raw.GetMediaPool(),
            error=errors.TimelineError,
            message="Could not get the project's media pool.",
            cause="GetMediaPool() returned None.",
        )
        raw = media_pool.CreateEmptyTimeline(name)
        if raw is None:
            raise errors.TimelineError(
                f"Could not create timeline {name!r}.",
                cause="CreateEmptyTimeline returned None — a timeline with this name may exist.",
                fix=f"Use `resolve.timeline.ensure({name!r})` for get-or-create.",
                state={"requested": name, "existing": self.names()},
            )
        return Timeline(raw, self._raw)

    def ensure(self, name: str) -> Timeline:
        if name in self.names():
            return self.get(name)
        return self.create(name)

    def set_current(self, timeline: Timeline | str) -> Timeline:
        target = timeline if isinstance(timeline, Timeline) else self.get(timeline)
        if not self._raw.SetCurrentTimeline(target.raw):
            raise errors.TimelineError(
                f"Could not set current timeline to {target.name!r}.",
                cause="SetCurrentTimeline returned False.",
                state={"requested": target.name},
            )
        return target

    @contextmanager
    def use(self, name: str) -> Iterator[Timeline]:
        previous = self.current
        target = self.set_current(name)
        try:
            yield target
        finally:
            if previous is not None and previous.name != name:
                try:
                    self.set_current(previous)
                except errors.DvrError as exc:
                    logger.warning("could not restore previous timeline %r: %s", previous.name, exc)

    def delete(self, timelines: Timeline | str | Iterable[Timeline | str]) -> None:
        media_pool = self._raw.GetMediaPool()
        if isinstance(timelines, (Timeline, str)):
            timelines = [timelines]
        raw_list = []
        for t in timelines:
            target = t if isinstance(t, Timeline) else self.get(t)
            raw_list.append(target.raw)
        if not media_pool.DeleteTimelines(raw_list):
            raise errors.TimelineError(
                "DeleteTimelines returned False.",
                state={"count": len(raw_list)},
            )


__all__ = [
    "Clip",  # deprecated alias for TimelineItem (within dvr.timeline namespace)
    "ClipFusion",  # deprecated alias for ItemFusion
    "ClipQuery",  # deprecated alias for ItemQuery
    "FusionComp",
    "FusionTool",
    "ItemEdit",
    "ItemFusion",
    "ItemQuery",
    "ItemText",
    "MarkerCollection",
    "Takes",
    "Timeline",
    "TimelineItem",
    "TimelineNamespace",
    "Track",
    "TrackCollection",
    "TrackList",
]
