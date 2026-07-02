"""MCP server implementation.

Each tool is a thin wrapper around a single library method. Tools are
declared with explicit JSON schemas so MCP clients (Claude, Cursor,
others) can show the LLM exactly what arguments are accepted and what
shape the response will take.

Errors come back as :class:`dvr.errors.DvrError.to_dict` payloads
inside the tool's text content (with ``isError=True``) so the LLM can
read the ``cause`` / ``fix`` / ``state`` fields and recover.

Tool registry
-------------

The registry lives in :func:`_build_registry`. Each entry pairs:

* a JSON schema understood by MCP clients,
* a description shown to the LLM,
* a handler ``(ctx, args) -> Any``,
* a flag indicating whether the handler needs a live Resolve connection.

Handlers that don't need Resolve (``version``, static schema topics,
``doctor``) work even without DaVinci Resolve installed or running --
useful for first-time setup and Claude Desktop diagnostics.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .. import __version__, errors
from ..media import MediaPool, scan_media_files
from ..resolve import Resolve

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, Resource, TextContent, Tool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The MCP server requires the `mcp` package. Reinstall with `pip install dvr`."
    ) from exc

logger = logging.getLogger("dvr.mcp")


def _brand_metadata() -> dict[str, str]:
    """Return MCP-discoverable branding assets bundled with the package."""
    assets = resources.files("dvr.mcp.assets")
    return {
        "name": "dvr",
        "logo": str(assets / "logo.png"),
        "icon": str(assets / "icon.png"),
    }


# ---------------------------------------------------------------------------
# Connection cache (one per server lifetime)
# ---------------------------------------------------------------------------


class _ResolveCache:
    """Lazily connect on first tool call; reuse for the rest of the session.

    Also caches connection *failures* for ``failure_ttl`` seconds. Without this,
    every failed tool call would spawn a fresh ``scriptapp()`` thread, and a
    series of failures (e.g. external scripting disabled) leaks daemon threads
    inside ``fusionscript.so`` that eventually deadlock the entire library.
    Caching the error lets the next 30+ tool calls return instantly with the
    same diagnostic instead.
    """

    def __init__(
        self,
        *,
        auto_launch: bool,
        timeout: float,
        failure_ttl: float = 30.0,
    ) -> None:
        self._auto_launch = auto_launch
        self._timeout = timeout
        self._failure_ttl = failure_ttl
        self._resolve: Resolve | None = None
        self._error: errors.DvrError | None = None
        self._error_at: float = 0.0

    def get(self) -> Resolve:
        """Return the cached :class:`Resolve` handle, connecting on first call.

        If the most recent connection attempt failed within the last
        ``failure_ttl`` seconds, re-raises the same structured error
        immediately rather than retrying.
        """
        import time

        if self._resolve is not None:
            return self._resolve
        if self._error is not None and (time.monotonic() - self._error_at) < self._failure_ttl:
            raise self._error
        try:
            self._resolve = Resolve(auto_launch=self._auto_launch, timeout=self._timeout)
            self._error = None
            self._error_at = 0.0
            return self._resolve
        except errors.DvrError as exc:
            self._error = exc
            self._error_at = time.monotonic()
            raise

    def reset(self) -> None:
        """Drop the cached connection and any cached error."""
        import contextlib

        if self._resolve is not None:
            with contextlib.suppress(Exception):  # boundary: best-effort cleanup
                self._resolve.close(cancel_pending_renders=False)
        self._resolve = None
        self._error = None
        self._error_at = 0.0


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ToolSpec:
    """One MCP tool: schema + handler + connection requirement."""

    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=lambda: _empty_schema())
    needs_resolve: bool = True
    handler: Callable[[_Context, dict[str, Any]], Any] = lambda ctx, args: None


@dataclass
class _Context:
    """Per-call context passed to tool handlers."""

    cache: _ResolveCache

    def resolve(self) -> Resolve:
        return self.cache.get()


def _empty_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


def _schema(
    properties: dict[str, dict[str, Any]],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = list(required)
    return out


def _current_project(ctx: _Context) -> Any:
    return ctx.resolve().project.require_current()


def _ensure_bin_path(media: Any, path: str | list[str]) -> Any:
    """Delegate to :meth:`dvr.media.MediaPool.ensure_folder_path` (duck-typed)."""
    return MediaPool.ensure_folder_path(media, path)


def _find_bin_path(media: Any, path: str | list[str]) -> Any:
    """Delegate to :meth:`dvr.media.MediaPool.find_folder_path` (duck-typed)."""
    return MediaPool.find_folder_path(media, path)


def _find_clip(
    media: Any, *, path: str | None = None, name: str | None = None, bin: str | None = None
) -> Any:
    if path:
        target = os.path.normcase(os.path.normpath(path))
        for folder in media.walk():
            for clip in folder.clips:
                clip_path = os.path.normcase(os.path.normpath(clip.file_path or ""))
                if clip_path == target:
                    return clip
        return media.find_or_import(path, folder=bin) if bin else media.find_or_import(path)
    if name:
        folders = [_find_bin_path(media, bin)] if bin else list(media.walk())
        for folder in folders:
            for clip in folder.clips:
                if clip.name == name:
                    return clip
    raise errors.MediaError(
        "Could not find media clip.",
        fix="Pass either `path` or `name` (and optionally `bin`).",
        state={"path": path, "name": name, "bin": bin},
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _h_version(_ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    return {
        "dvr": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "brand": _brand_metadata(),
    }


def _h_doctor(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    """Diagnose the dvr <-> Resolve setup without raising on failure.

    By default this is a fast static probe (no connection attempt). Pass
    ``probe=true`` to additionally try a live connection (may take several
    seconds while macOS LAN IPs and pinghosts are tried).
    """
    from ..doctor import diagnose

    out = diagnose(probe=False)
    out["brand"] = _brand_metadata()
    # Whether the long-lived MCP cache already has a live connection or
    # is in the failure-cooldown window from a recent failed connect.
    out["connection_cached"] = ctx.cache._resolve is not None
    out["last_connection_error"] = (
        ctx.cache._error.to_dict() if ctx.cache._error is not None else None
    )
    if not bool(args.get("probe", False)):
        return out

    # Probe through the MCP connection cache (not a fresh Resolve()) so a
    # successful probe warms the cache for subsequent tool calls.
    try:
        r = ctx.cache.get()
        out["connected"] = True
        out["resolve_version"] = r.app.version
        out["resolve_product"] = r.app.product
        out["current_project"] = r.project.current.name if r.project.current is not None else None
    except errors.DvrError as exc:
        out["connected"] = False
        out["connection_error"] = exc.to_dict()
    except Exception as exc:  # boundary
        out["connected"] = False
        out["connection_error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    return out


def _h_reconnect(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    ctx.cache.reset()
    r = ctx.cache.get()
    return {
        "reconnected": True,
        "version": r.app.version,
        "product": r.app.product,
    }


def _h_ping(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    return {"connected": True, "version": r.app.version, "product": r.app.product}


def _h_inspect(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    return ctx.resolve().inspect()


def _h_page_get(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    return {"page": str(ctx.resolve().app.page)}


def _h_page_set(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    r.app.page = args["name"]
    return {"page": str(r.app.page)}


def _h_project_list(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": n} for n in ctx.resolve().project.list()]


def _h_project_ensure(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    return ctx.resolve().project.ensure(args["name"]).inspect()


def _h_project_current(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    current = ctx.resolve().project.current
    return current.inspect() if current else {"current": None}


def _h_project_settings_get(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    keys = args.get("keys")
    if keys:
        return {str(key): current.get_setting(str(key)) for key in keys}
    settings = current.get_setting()
    return dict(settings) if isinstance(settings, dict) else {"settings": settings}


def _h_project_save(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    current.save()
    return {"saved": current.name}


def _h_project_delete(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    name = args["name"]
    current = ctx.resolve().project.current
    if bool(args.get("close_current", True)) and current is not None and current.name == name:
        current.close()
    ctx.resolve().project.delete(name)
    return {"deleted": name}


def _h_timeline_list(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"name": tl.name, "fps": tl.fps, "duration": tl.duration_frames}
        for tl in ctx.resolve().timeline.list()
    ]


def _h_timeline_inspect(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    requested = args.get("name")
    tl = r.timeline.get(requested) if requested else r.timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")
    return tl.inspect()


def _h_timeline_ensure(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    return ctx.resolve().timeline.ensure(args["name"]).inspect()


def _h_timeline_switch(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = ctx.resolve().timeline.set_current(args["name"])
    return {"current": tl.name}


def _h_timeline_rename(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = ctx.resolve().timeline.get(args["name"])
    old = tl.name
    tl.name = args["new_name"]
    return {"renamed": old, "name": tl.name}


def _h_timeline_delete(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    ctx.resolve().timeline.delete(args["name"])
    return {"deleted": args["name"]}


def _h_timeline_clear(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    tl = r.timeline.get(args["timeline"]) if args.get("timeline") else r.timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")
    track_type = args.get("track_type")
    track_indexes = args.get("track_indexes")
    ripple = bool(args.get("ripple", False))
    items: list[Any] = []
    if track_indexes and not track_type:
        raise errors.TimelineError(
            "timeline_clear requires track_type when track_indexes is provided.",
            fix="Pass track_type='video', 'audio', or 'subtitle' with track_indexes.",
            state={"track_indexes": track_indexes},
        )
    if track_type and track_indexes:
        for index in track_indexes:
            items.extend(tl.track(track_type, int(index)).items)
    elif track_type:
        items.extend(tl.items(track_type))
    else:
        items.extend(tl.items())
    tl.delete_clips(items, ripple=ripple)
    return {"timeline": tl.name, "deleted": len(items), "ripple": ripple}


def _h_marker_add(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    name = args.get("timeline")
    tl = r.timeline.get(name) if name else r.timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")
    tl.add_marker(
        int(args["frame"]),
        color=args.get("color", "Blue"),
        name=args.get("name", ""),
        note=args.get("note", ""),
        duration=int(args.get("duration", 1)),
        custom_data=args.get("custom_data", ""),
    )
    return {
        "added": True,
        "timeline": tl.name,
        "frame": int(args["frame"]),
    }


def _timeline_for_args(ctx: _Context, args: dict[str, Any]) -> Any:
    r = ctx.resolve()
    name = args.get("timeline")
    tl = r.timeline.get(name) if name else r.timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")
    return tl


def _select_timeline_items(ctx: _Context, args: dict[str, Any]) -> tuple[Any, list[Any]]:
    tl = _timeline_for_args(ctx, args)
    track_type = args.get("track_type")
    track_index = args.get("track_index")
    duration_lt = args.get("duration_lt")
    duration_gt = args.get("duration_gt")
    name_exact = args.get("name")
    name_contains = args.get("name_contains")

    items: list[Any] = []
    if track_type and track_index is not None:
        items.extend(tl.track(track_type, int(track_index)).items)
    elif track_type:
        items.extend(tl.items(track_type))
    else:
        items.extend(tl.items())

    def matches(item: Any) -> bool:
        if track_index is not None and int(item.track_index) != int(track_index):
            return False
        if duration_lt is not None and not item.duration < int(duration_lt):
            return False
        if duration_gt is not None and not item.duration > int(duration_gt):
            return False
        if name_exact is not None and item.name != name_exact:
            return False
        return not (name_contains is not None and name_contains not in (item.name or ""))

    return tl, [it for it in items if matches(it)]


def _h_clip_where(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter clips on a timeline using safe predicate fields.

    The MCP boundary deliberately does *not* expose a Python lambda --
    instead, callers pick from a small, declarative DSL of safe filters.
    """
    args = {"track_type": "video", **args}
    _, matched = _select_timeline_items(ctx, args)
    return [
        {
            "name": it.name,
            "track_type": it.track_type,
            "track_index": it.track_index,
            "duration_frames": it.duration,
            "start_frame": it.start,
            "end_frame": it.end,
        }
        for it in matched
    ]


def _h_clip_set_properties(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import schema as schema_mod

    tl, items = _select_timeline_items(ctx, args)
    normalized = schema_mod.normalize_clip_properties(dict(args["properties"]))
    if bool(args.get("dry_run", False)):
        return {
            "timeline": tl.name,
            "would_update": [it.name for it in items],
            "count": len(items),
            "properties": normalized,
        }
    for item in items:
        item.set_properties(normalized)
    return {"timeline": tl.name, "updated": len(items), "properties": normalized}


def _h_clip_transform(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    props = {
        key: args[key]
        for key in (
            "pan",
            "tilt",
            "zoom",
            "zoom_x",
            "zoom_y",
            "rotation",
            "anchor_x",
            "anchor_y",
            "pitch",
            "yaw",
            "flip_x",
            "flip_y",
        )
        if key in args
    }
    return _h_clip_set_properties(ctx, {**args, "properties": props})


def _h_clip_crop(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    props = {
        key: args[key]
        for key in (
            "crop_left",
            "crop_right",
            "crop_top",
            "crop_bottom",
            "crop_softness",
            "crop_retain",
        )
        if key in args
    }
    return _h_clip_set_properties(ctx, {**args, "properties": props})


def _h_clip_reset(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import schema as schema_mod

    groups = args.get("groups")
    props = schema_mod.reset_clip_properties(groups)
    return _h_clip_set_properties(ctx, {**args, "properties": props})


def _h_clip_capabilities(_ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    from .. import schema as schema_mod

    return schema_mod.clip_property_capabilities()


def _h_render_queue(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    return list(ctx.resolve().render.queue())


def _h_render_presets(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": n} for n in ctx.resolve().render.presets()]


def _h_render_formats(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"format": k, "extension": v} for k, v in ctx.resolve().render.formats().items()]


def _h_render_codecs(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"codec": k, "label": v} for k, v in ctx.resolve().render.codecs(args["format"]).items()
    ]


def _h_render_submit(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    job = r.render.submit(
        target_dir=args["target_dir"],
        custom_name=args.get("custom_name"),
        preset=args.get("preset"),
        format=args.get("format"),
        codec=args.get("codec"),
        start=bool(args.get("start", True)),
    )
    return {"job_id": job.id, "started": bool(args.get("start", True))}


def _h_render_status(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    return ctx.resolve().render.status(args["job_id"])


def _h_render_stop(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    ctx.resolve().render.stop()
    return {"stopped": True}


def _h_render_clear(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    ctx.resolve().render.clear()
    return {"cleared": True}


def _h_media_inspect(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    return _current_project(ctx).media.inspect()


def _h_media_bins(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    return [b.inspect() for b in _current_project(ctx).media.root.subbins()]


def _h_media_ls(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    media = _current_project(ctx).media
    target = _find_bin_path(media, args["bin"]) if args.get("bin") else media.root
    return [a.inspect() for a in target.assets()]


def _h_media_import(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    media = _current_project(ctx).media
    target_bin = _ensure_bin_path(media, args["bin"]) if args.get("bin") else None
    assets = media.import_(args["paths"], bin=target_bin)
    return [a.inspect() for a in assets]


def _h_media_scan(_ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    files = scan_media_files(
        args["path"],
        recursive=bool(args.get("recursive", True)),
        include_hidden=bool(args.get("include_hidden", False)),
        max_files=int(args.get("max_files", 10000)),
    )
    counts: dict[str, int] = {}
    for item in files:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {
        "path": str(Path(args["path"]).expanduser()),
        "file_count": len(files),
        "counts": counts,
        "files": files,
    }


def _h_media_bin_ensure(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    folder = _ensure_bin_path(current.media, args["path"])
    return folder.inspect()


def _h_media_move(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    media = current.media
    source = _find_bin_path(media, args["source_bin"]) if args.get("source_bin") else media.root
    target = _ensure_bin_path(media, args["target_bin"])
    name_contains = args.get("name_contains")
    kind = args.get("kind")
    paths = set(args.get("paths") or [])

    clips = []
    folders = [source] if not bool(args.get("recursive", False)) else list(source.walk())
    for folder in folders:
        for clip in folder.clips:
            if name_contains and name_contains not in clip.name:
                continue
            if kind and clip.kind != kind:
                continue
            if paths and clip.file_path not in paths:
                continue
            clips.append(clip)

    if clips:
        current.media.move(clips, target)
    return {"moved": len(clips), "target_bin": target.name}


def _h_media_bin_delete(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    folder = _find_bin_path(current.media, args["path"])
    name = folder.name
    folder.delete()
    return {"deleted": name, "path": args["path"]}


# ---- AI / Studio (Resolve 21+) -------------------------------------------


def _ai_media_target(ctx: _Context, args: dict[str, Any]) -> Any:
    """Resolve the AI target: a single clip (``clip``) or a bin/folder (``bin``)."""
    media = _current_project(ctx).media
    if args.get("clip"):
        return _find_clip(media, name=args["clip"], bin=args.get("bin"))
    return _find_bin_path(media, args["bin"]) if args.get("bin") else media.root


def _h_media_transcribe(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    target = _ai_media_target(ctx, args)
    target.transcribe(use_speaker_detection=args.get("speaker_detection"))
    return {"transcribed": target.name, "speaker_detection": args.get("speaker_detection")}


def _h_media_classify_audio(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    target = _ai_media_target(ctx, args)
    if bool(args.get("clear", False)):
        target.clear_audio_classification()
        return {"cleared_classification": target.name}
    target.classify_audio()
    return {"classified": target.name}


def _h_media_deblur(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    target = _ai_media_target(ctx, args)
    options: dict[str, Any] = {}
    if args.get("format"):
        options["Format"] = args["format"]
    if args.get("codec"):
        options["Codec"] = args["codec"]
    if bool(args.get("extreme", False)):
        options["UseExtremeMode"] = True
    result = target.remove_motion_blur(options or None)
    if isinstance(result, list):
        return {
            "deblurred": [{"original": orig.name, "deblurred": new.name} for orig, new in result]
        }
    return {"deblurred": result.name if result is not None else None}


def _h_media_analyze(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    target = _ai_media_target(ctx, args)
    kind = args["kind"]
    if kind == "intellisearch":
        ok = target.analyze_for_intellisearch(
            identify_faces=bool(args.get("faces", False)),
            better_mode=bool(args.get("better", False)),
        )
    elif kind == "slate":
        ok = target.analyze_for_slate(args.get("color", "Blue"))
    else:
        raise errors.MediaError(
            f"Unknown analysis kind {kind!r}.",
            fix="Use 'intellisearch' or 'slate'.",
        )
    return {"analyzed": target.name, "kind": kind, "ok": bool(ok)}


def _h_project_reset_intellisearch(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    current.reset_intellisearch_analysis()
    return {"reset_intellisearch": current.name}


def _h_project_generate_speech(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    settings: dict[str, Any] = {
        "TextInput": args["text"],
        "AddToTimeline": bool(args.get("add_to_timeline", True)),
    }
    if args.get("voice"):
        settings["VoiceModel"] = args["voice"]
    if args.get("speed") is not None:
        settings["Speed"] = float(args["speed"])
    if args.get("pitch") is not None:
        settings["Pitch"] = float(args["pitch"])
    if args.get("filename"):
        settings["Filename"] = args["filename"]
    if args.get("track") is not None:
        settings["AudioTrack"] = int(args["track"])
    timecode = args.get("timecode", "01:00:00:00")
    clip = current.generate_speech(settings, timecode)
    return {"generated": clip.name, "timecode": timecode}


def _h_disable_background_tasks(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    ctx.resolve().app.disable_background_tasks()
    return {"background_tasks_disabled": True}


def _h_timeline_append(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    current = _current_project(ctx)
    media = current.media
    timeline_name = args.get("timeline")
    tl = current.timeline.set_current(timeline_name) if timeline_name else current.timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")

    requested_items = args["items"]
    for item in requested_items:
        track_index = int(item.get("track_index", 1))
        media_type = item.get("media_type")
        if track_index > 1 and "record_frame" not in item:
            raise errors.TimelineError(
                "timeline_append requires record_frame for non-default track targets.",
                cause=(
                    "DaVinci Resolve's AppendToTimeline does not maintain a separate "
                    "end-of-track cursor for V2/A2+ targets."
                ),
                fix="Pass an explicit `record_frame` for every item targeting track_index >= 2.",
                state={"item": item},
            )
        if media_type in ("video", "audio"):
            track_type = media_type
            while tl.track_count(track_type) < track_index:
                tl.add_track(track_type)

    payload: list[dict[str, Any]] = []
    for item in requested_items:
        clip = _find_clip(
            media,
            path=item.get("path"),
            name=item.get("name"),
            bin=item.get("bin"),
        )
        entry: dict[str, Any] = {"mediaPoolItem": clip.raw}
        if item.get("media_type"):
            entry["mediaType"] = 1 if item["media_type"] == "video" else 2
        for key, resolve_key in (
            ("track_index", "trackIndex"),
            ("record_frame", "recordFrame"),
            ("start_frame", "startFrame"),
            ("end_frame", "endFrame"),
        ):
            if key in item:
                entry[resolve_key] = int(item[key])
        payload.append(entry)
    appended = media.append_to_timeline(payload)
    if len(appended) != len(payload):
        raise errors.TimelineError(
            "Resolve appended fewer timeline items than requested.",
            cause="AppendToTimeline returned a partial result; Resolve may have rejected track targeting.",
            fix="Use explicit `record_frame` values and inspect the timeline before retrying.",
            state={"requested_count": len(payload), "appended_count": len(appended)},
        )
    return {"timeline": tl.name, "appended": len(appended)}


def _text_fields_from_args(args: dict[str, Any]) -> dict[str, Any]:
    """Collect Text+ styling fields from MCP args (dropping unset ones)."""
    fields: dict[str, Any] = {}
    for key in (
        "text",
        "font",
        "style",
        "size",
        "color",
        "opacity",
        "tracking",
        "line_spacing",
        "align",
        "vertical_align",
    ):
        if args.get(key) is not None:
            fields[key] = args[key]
    if args.get("pos_x") is not None and args.get("pos_y") is not None:
        fields["position"] = (float(args["pos_x"]), float(args["pos_y"]))
    return fields


def _h_timeline_add_title(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = _timeline_for_args(ctx, args)
    if args.get("timecode"):
        tl.current_timecode = args["timecode"]
    fields = _text_fields_from_args(args)
    item = tl.insert_title(
        args.get("title", "Text+"),
        fusion=bool(args.get("fusion", True)),
        **fields,
    )
    return {
        "timeline": tl.name,
        "inserted": item.name,
        "title": args.get("title", "Text+"),
        "fusion": bool(args.get("fusion", True)),
        "styled": sorted(fields),
    }


def _h_clip_set_text(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    args = {"track_type": "video", **args}
    tl, items = _select_timeline_items(ctx, args)
    fields = _text_fields_from_args(args)
    if bool(args.get("dry_run", False)):
        return {
            "timeline": tl.name,
            "would_update": [it.name for it in items],
            "count": len(items),
            "fields": sorted(fields),
        }
    updated: list[str] = []
    skipped: list[dict[str, str]] = []
    for item in items:
        try:
            item.text.set(**fields)
            updated.append(item.name)
        except errors.FusionError as exc:
            skipped.append({"clip": item.name, "reason": str(exc)})
    return {"timeline": tl.name, "updated": updated, "skipped": skipped, "count": len(updated)}


def _h_timeline_create_subtitles(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = _timeline_for_args(ctx, args)
    tl.create_subtitles_from_audio(
        language=args.get("language", "auto"),
        chars_per_line=int(args.get("chars_per_line", 42)),
        line_break_type=args.get("line_break_type", "Auto"),
        preset=args.get("preset"),
    )
    return {"timeline": tl.name, "subtitles_created": True}


def _h_timeline_set_start_timecode(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = _timeline_for_args(ctx, args)
    tl.start_timecode = args["timecode"]
    return {"timeline": tl.name, "start_timecode": args["timecode"]}


def _h_timeline_add_generator(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = _timeline_for_args(ctx, args)
    if args.get("timecode"):
        tl.current_timecode = args["timecode"]
    item = tl.insert_generator(
        args["name"],
        fusion=bool(args.get("fusion", False)),
        ofx=bool(args.get("ofx", False)),
    )
    return {"timeline": tl.name, "inserted": item.name, "generator": args["name"]}


def _h_timeline_grab_stills(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = _timeline_for_args(ctx, args)
    src = 2 if str(args.get("source", "first")).lower() == "middle" else 1
    stills = tl.grab_all_stills(src)
    return {"timeline": tl.name, "grabbed": len(stills)}


def _h_timeline_import_into(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    tl = _timeline_for_args(ctx, args)
    tl.import_into(args["file_path"], args.get("options"))
    return {"timeline": tl.name, "imported": args["file_path"]}


def _h_render_mode(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    if args.get("set"):
        r.render.set_render_mode(args["set"])
    return {"render_mode": r.render.render_mode()}


def _h_render_resolutions(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    r = ctx.resolve()
    return r.render.resolutions(args.get("format"), args.get("codec"))


def _h_render_refresh_luts(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    ctx.resolve().render.refresh_lut_list()
    return {"refreshed": True}


def _h_project_color_groups(ctx: _Context, args: dict[str, Any]) -> Any:
    proj = _current_project(ctx)
    if args.get("add"):
        group = proj.add_color_group(args["add"])
        return {"created": group.name}
    if args.get("delete"):
        match = next((g for g in proj.color_groups() if g.name == args["delete"]), None)
        if match is None:
            raise errors.ColorError(f"No color group named {args['delete']!r}.")
        proj.delete_color_group(match)
        return {"deleted": args["delete"]}
    return [{"name": g.name} for g in proj.color_groups()]


def _h_project_export_still(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    proj = _current_project(ctx)
    proj.export_current_frame_as_still(args["file_path"])
    return {"exported": args["file_path"]}


def _h_media_export_metadata(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    proj = _current_project(ctx)
    proj.media.export_metadata(args["file_path"])
    return {"exported": args["file_path"]}


def _h_media_import_bin(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    proj = _current_project(ctx)
    proj.media.import_folder_from_file(
        args["file_path"], source_clips_path=args.get("source_clips", "")
    )
    return {"imported": args["file_path"]}


def _h_interchange_export(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import interchange

    tl = ctx.resolve().timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")
    path = interchange.export(
        tl,
        args["file_path"],
        format=args.get("format", "fcpxml-1.10"),
    )
    return {"exported": path}


def _h_diff_timelines(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import diff

    r = ctx.resolve()
    left = r.timeline.get(args["a"])
    right = r.timeline.get(args["b"])
    return diff.compare_timelines(left, right).to_dict()


def _h_diff_to_spec(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import diff
    from .. import spec as spec_mod

    parsed = spec_mod.load_spec(args["spec_path"])
    return diff.compare_to_spec(ctx.resolve(), parsed).to_dict()


def _h_apply_spec(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import spec as spec_mod

    parsed = spec_mod.load_spec(args["spec_path"])
    actions = spec_mod.apply(
        parsed,
        ctx.resolve(),
        dry_run=bool(args.get("dry_run", False)),
        run_hooks=bool(args.get("run_hooks", True)),
        continue_on_error=bool(args.get("continue_on_error", False)),
        transactional=bool(args.get("transactional", False)),
        verify=bool(args.get("verify", False)),
    )
    return {
        "spec": str(args["spec_path"]),
        "dry_run": bool(args.get("dry_run", False)),
        "actions": [
            {"op": a.op, "target": a.target, "detail": a.detail, "payload": a.payload}
            for a in actions
        ],
    }


def _h_snapshot_save(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import snapshot as snap_mod

    snap = snap_mod.capture(ctx.resolve(), name=args.get("name") or None)
    snap_path = snap_mod.save(snap)
    return {
        "name": snap.name,
        "project": snap.project,
        "captured_at": snap.captured_at,
        "path": str(snap_path),
    }


def _h_spec_export(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import spec as spec_mod

    return spec_mod.from_live(ctx.resolve(), project=args.get("project"))


def _h_render_wait(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from ..render import RenderJob

    job = RenderJob(ctx.resolve().render, str(args["job_id"]))
    job.wait(
        poll_interval=float(args.get("poll_interval", 1.0)),
        timeout=float(args["timeout"]) if args.get("timeout") is not None else 600.0,
    )
    return job.poll()


def _h_timeline_assemble(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    """Workflow tool: import media and assemble a timeline in one call."""
    current = _current_project(ctx)
    media = current.media
    tl = current.timeline.ensure(str(args["timeline"]))
    if args.get("fps") is not None:
        tl.set_setting("timelineFrameRate", str(args["fps"]))

    target_bin = _ensure_bin_path(media, args["bin"]) if args.get("bin") else None

    imported = 0
    payload: list[dict[str, Any]] = []
    for item in args["items"]:
        if item.get("path"):
            clip = media.find_or_import(item["path"], folder=target_bin)
            imported += 1
        else:
            clip = _find_clip(media, name=item.get("name"), bin=args.get("bin"))
        entry: dict[str, Any] = {"mediaPoolItem": clip.raw}
        for key, resolve_key in (
            ("start_frame", "startFrame"),
            ("end_frame", "endFrame"),
        ):
            if item.get(key) is not None:
                entry[resolve_key] = int(item[key])
        payload.append(entry)

    appended = media.append_to_timeline(payload)
    if len(appended) != len(payload):
        raise errors.TimelineError(
            "Resolve appended fewer timeline items than requested.",
            cause="AppendToTimeline returned a partial result.",
            fix="Inspect the timeline with `timeline_inspect` before retrying.",
            state={"requested_count": len(payload), "appended_count": len(appended)},
        )
    return {
        "timeline": tl.name,
        "appended": len(appended),
        "imported": imported,
        "duration_frames": tl.duration_frames,
    }


def _h_snapshot_list(_ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    from .. import snapshot as snap_mod

    return [
        {"name": s.name, "project": s.project, "captured_at": s.captured_at}
        for s in snap_mod.list_snapshots()
    ]


def _h_snapshot_restore(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    from .. import snapshot as snap_mod

    snap = snap_mod.load(args["name"])
    counts = snap_mod.restore(ctx.resolve(), snap, dry_run=bool(args.get("dry_run", False)))
    return {"snapshot": snap.name, "project": snap.project, **counts}


def _h_lint(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    from .. import lint as lint_mod

    return lint_mod.lint(ctx.resolve()).to_dict()


def _h_schema(ctx: _Context, args: dict[str, Any]) -> Any:
    from .. import schema as schema_mod

    topic = args["topic"]
    needs_live = topic in ("render-formats", "render-codecs", "render-presets")
    if needs_live:
        return schema_mod.get_topic(topic, ctx.resolve())
    return schema_mod.get_topic(topic)


def _h_eval(ctx: _Context, args: dict[str, Any]) -> Any:
    if os.environ.get("DVR_MCP_ENABLE_EVAL", "0") not in ("1", "true", "yes"):
        raise errors.DvrError(
            "The `eval` tool is disabled by default.",
            cause=(
                "Arbitrary Python execution is risky in agent contexts; "
                "DVR_MCP_ENABLE_EVAL is not set."
            ),
            fix=(
                "Restart the MCP server with DVR_MCP_ENABLE_EVAL=1 in its environment "
                "if you really want to enable eval."
            ),
        )
    import dvr as _dvr

    r = ctx.resolve()
    project = r.project.current
    timeline = project.timeline.current if project else None
    ns = {"r": r, "project": project, "timeline": timeline, "dvr": _dvr}
    value = eval(args["expression"], ns)
    if hasattr(value, "inspect") and callable(value.inspect):
        value = value.inspect()
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    return value


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_PAGE_NAMES = ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver")


def _build_registry() -> list[_ToolSpec]:
    return [
        # ---- meta / no Resolve required --------------------------------
        _ToolSpec(
            name="version",
            description="Return the dvr package version, Python version, and platform.",
            handler=_h_version,
            needs_resolve=False,
        ),
        _ToolSpec(
            name="doctor",
            description=(
                "Diagnose the dvr -> DaVinci Resolve setup. Reports scripting library "
                "presence, environment vars, whether Resolve is running, and any "
                "structured connection error. Fast by default; pass probe=true to also "
                "attempt a live connection. Never raises."
            ),
            schema=_schema(
                {
                    "probe": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "If true, attempt a live connection. May block several "
                            "seconds while LAN IPs are tried."
                        ),
                    }
                }
            ),
            handler=_h_doctor,
            needs_resolve=False,
        ),
        _ToolSpec(
            name="reconnect",
            description=(
                "Drop the cached Resolve connection and reconnect. Use after "
                "Resolve was relaunched or external scripting was just enabled."
            ),
            handler=_h_reconnect,
            needs_resolve=False,
        ),
        _ToolSpec(
            name="schema",
            description=(
                "Discoverable catalog of valid setting keys, codecs, properties. "
                "Topics include clip-properties, clip-property-aliases, "
                "clip-property-defaults, clip-capabilities, settings, export-formats, "
                "color-presets, render-formats, render-codecs, render-presets. "
                "Live topics (render-*) require an active Resolve connection; "
                "static topics do not."
            ),
            schema=_schema(
                {
                    "topic": {
                        "type": "string",
                        "enum": [
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
                        ],
                    }
                },
                required=["topic"],
            ),
            handler=_h_schema,
            needs_resolve=False,  # static topics skip the connect; live topics
            # connect lazily inside the handler.
        ),
        _ToolSpec(
            name="snapshot_list",
            description="List snapshots on disk, newest first. Does not require Resolve.",
            handler=_h_snapshot_list,
            needs_resolve=False,
        ),
        # ---- live: app + project + timeline ----------------------------
        _ToolSpec(
            name="ping",
            description="Verify the connection to DaVinci Resolve. Returns version info.",
            handler=_h_ping,
        ),
        _ToolSpec(
            name="inspect",
            description=(
                "One-call snapshot of app, current project, and current timeline. "
                "Most efficient way to read state before deciding what to do."
            ),
            handler=_h_inspect,
        ),
        _ToolSpec(
            name="page_get",
            description="Read the current Resolve page (media|cut|edit|fusion|color|fairlight|deliver).",
            handler=_h_page_get,
        ),
        _ToolSpec(
            name="page_set",
            description="Switch to a Resolve page.",
            schema=_schema(
                {"name": {"type": "string", "enum": list(_PAGE_NAMES)}},
                required=["name"],
            ),
            handler=_h_page_set,
        ),
        _ToolSpec(
            name="project_list",
            description="List projects in the current Project Manager folder.",
            handler=_h_project_list,
        ),
        _ToolSpec(
            name="project_ensure",
            description="Load a project by name, creating it if it does not exist. Idempotent.",
            schema=_schema({"name": {"type": "string"}}, required=["name"]),
            handler=_h_project_ensure,
        ),
        _ToolSpec(
            name="project_current",
            description="Inspect the currently loaded project.",
            handler=_h_project_current,
        ),
        _ToolSpec(
            name="project_settings_get",
            description="Read project settings from the current project. Pass keys to limit output.",
            schema=_schema({"keys": {"type": "array", "items": {"type": "string"}}}),
            handler=_h_project_settings_get,
        ),
        _ToolSpec(
            name="project_save",
            description="Save the currently loaded project.",
            handler=_h_project_save,
        ),
        _ToolSpec(
            name="project_delete",
            description="Delete a project by name. Closes it first when it is the current project.",
            schema=_schema(
                {
                    "name": {"type": "string"},
                    "close_current": {"type": "boolean", "default": True},
                },
                required=["name"],
            ),
            handler=_h_project_delete,
        ),
        _ToolSpec(
            name="timeline_list",
            description="List timelines in the currently loaded project.",
            handler=_h_timeline_list,
        ),
        _ToolSpec(
            name="timeline_inspect",
            description="Return a structured snapshot of a timeline (tracks, clips, markers).",
            schema=_schema(
                {
                    "name": {
                        "type": "string",
                        "description": "Timeline name. Defaults to the current timeline.",
                    }
                }
            ),
            handler=_h_timeline_inspect,
        ),
        _ToolSpec(
            name="timeline_ensure",
            description="Get-or-create a timeline by name in the current project. Idempotent.",
            schema=_schema({"name": {"type": "string"}}, required=["name"]),
            handler=_h_timeline_ensure,
        ),
        _ToolSpec(
            name="timeline_switch",
            description="Set a timeline as the current one.",
            schema=_schema({"name": {"type": "string"}}, required=["name"]),
            handler=_h_timeline_switch,
        ),
        _ToolSpec(
            name="timeline_rename",
            description="Rename a timeline in the current project.",
            schema=_schema(
                {"name": {"type": "string"}, "new_name": {"type": "string"}},
                required=["name", "new_name"],
            ),
            handler=_h_timeline_rename,
        ),
        _ToolSpec(
            name="timeline_delete",
            description="Delete a timeline from the current project.",
            schema=_schema({"name": {"type": "string"}}, required=["name"]),
            handler=_h_timeline_delete,
        ),
        _ToolSpec(
            name="timeline_clear",
            description=(
                "Delete timeline items from the current or named timeline. Can be scoped "
                "by track_type and 1-based track_indexes."
            ),
            schema=_schema(
                {
                    "timeline": {"type": "string"},
                    "track_type": {"type": "string", "enum": ["video", "audio", "subtitle"]},
                    "track_indexes": {"type": "array", "items": {"type": "integer"}},
                    "ripple": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_timeline_clear,
        ),
        _ToolSpec(
            name="marker_add",
            description="Add a marker to a timeline at the given frame.",
            schema=_schema(
                {
                    "frame": {"type": "integer"},
                    "color": {
                        "type": "string",
                        "description": "Marker color (Resolve marker palette).",
                        "default": "Blue",
                    },
                    "name": {"type": "string"},
                    "note": {"type": "string"},
                    "duration": {"type": "integer", "default": 1},
                    "custom_data": {"type": "string"},
                    "timeline": {
                        "type": "string",
                        "description": "Timeline name. Defaults to the current timeline.",
                    },
                },
                required=["frame"],
            ),
            handler=_h_marker_add,
        ),
        _ToolSpec(
            name="clip_where",
            description=(
                "Find timeline items by safe declarative filters. Returns a list "
                "of {name, track_type, duration_frames, start_frame, end_frame}."
            ),
            schema=_schema(
                {
                    "track_type": {
                        "type": "string",
                        "enum": ["video", "audio", "subtitle"],
                        "default": "video",
                    },
                    "duration_lt": {
                        "type": "integer",
                        "description": "Match items with duration (frames) strictly less than this.",
                    },
                    "duration_gt": {
                        "type": "integer",
                        "description": "Match items with duration (frames) strictly greater than this.",
                    },
                    "name_contains": {
                        "type": "string",
                        "description": "Substring match on the item name.",
                    },
                    "timeline": {
                        "type": "string",
                        "description": "Timeline name. Defaults to the current timeline.",
                    },
                }
            ),
            handler=_h_clip_where,
        ),
        _ToolSpec(
            name="clip_set_properties",
            description=(
                "Set documented TimelineItem properties on clips selected by safe filters. "
                "Accepts friendly keys like crop_top, zoom, blend, and enum names."
            ),
            schema=_schema(
                {
                    "properties": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "TimelineItem SetProperty keys or friendly aliases.",
                    },
                    "timeline": {"type": "string"},
                    "track_type": {"type": "string", "enum": ["video", "audio", "subtitle"]},
                    "track_index": {"type": "integer"},
                    "name": {"type": "string"},
                    "name_contains": {"type": "string"},
                    "duration_lt": {"type": "integer"},
                    "duration_gt": {"type": "integer"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                required=["properties"],
            ),
            handler=_h_clip_set_properties,
        ),
        _ToolSpec(
            name="clip_transform",
            description="Set transform properties on selected timeline clips.",
            schema=_schema(
                {
                    "pan": {"type": "number"},
                    "tilt": {"type": "number"},
                    "zoom": {"type": "number", "description": "Sets ZoomX and ZoomY together."},
                    "zoom_x": {"type": "number"},
                    "zoom_y": {"type": "number"},
                    "rotation": {"type": "number"},
                    "anchor_x": {"type": "number"},
                    "anchor_y": {"type": "number"},
                    "pitch": {"type": "number"},
                    "yaw": {"type": "number"},
                    "flip_x": {"type": "boolean"},
                    "flip_y": {"type": "boolean"},
                    "timeline": {"type": "string"},
                    "track_type": {"type": "string", "enum": ["video", "audio", "subtitle"]},
                    "track_index": {"type": "integer"},
                    "name": {"type": "string"},
                    "name_contains": {"type": "string"},
                    "duration_lt": {"type": "integer"},
                    "duration_gt": {"type": "integer"},
                    "dry_run": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_clip_transform,
        ),
        _ToolSpec(
            name="clip_crop",
            description="Set crop properties on selected timeline clips.",
            schema=_schema(
                {
                    "crop_left": {"type": "number"},
                    "crop_right": {"type": "number"},
                    "crop_top": {"type": "number"},
                    "crop_bottom": {"type": "number"},
                    "crop_softness": {"type": "number"},
                    "crop_retain": {"type": "boolean"},
                    "timeline": {"type": "string"},
                    "track_type": {"type": "string", "enum": ["video", "audio", "subtitle"]},
                    "track_index": {"type": "integer"},
                    "name": {"type": "string"},
                    "name_contains": {"type": "string"},
                    "duration_lt": {"type": "integer"},
                    "duration_gt": {"type": "integer"},
                    "dry_run": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_clip_crop,
        ),
        _ToolSpec(
            name="clip_reset",
            description=(
                "Reset documented clip editing property groups. Groups include transform, "
                "crop, composite, retime, scaling, and dynamic_zoom."
            ),
            schema=_schema(
                {
                    "groups": {"type": "array", "items": {"type": "string"}},
                    "timeline": {"type": "string"},
                    "track_type": {"type": "string", "enum": ["video", "audio", "subtitle"]},
                    "track_index": {"type": "integer"},
                    "name": {"type": "string"},
                    "name_contains": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_clip_reset,
        ),
        _ToolSpec(
            name="clip_capabilities",
            description=(
                "Return what Resolve exposes for timeline-item editing, including unsupported "
                "transition/keyframe capabilities."
            ),
            handler=_h_clip_capabilities,
            needs_resolve=False,
        ),
        # ---- render ----------------------------------------------------
        _ToolSpec(
            name="render_queue",
            description="List jobs in the render queue.",
            handler=_h_render_queue,
        ),
        _ToolSpec(
            name="render_presets",
            description="List available render presets.",
            handler=_h_render_presets,
        ),
        _ToolSpec(
            name="render_formats",
            description="List render container formats.",
            handler=_h_render_formats,
        ),
        _ToolSpec(
            name="render_codecs",
            description="List codecs available for a container format.",
            schema=_schema({"format": {"type": "string"}}, required=["format"]),
            handler=_h_render_codecs,
        ),
        _ToolSpec(
            name="render_submit",
            description="Configure and queue a render of the current timeline. Returns a job_id.",
            schema=_schema(
                {
                    "target_dir": {"type": "string"},
                    "custom_name": {"type": "string"},
                    "preset": {"type": "string"},
                    "format": {"type": "string"},
                    "codec": {"type": "string"},
                    "start": {"type": "boolean", "default": True},
                },
                required=["target_dir"],
            ),
            handler=_h_render_submit,
        ),
        _ToolSpec(
            name="render_status",
            description="Get the status of a render job.",
            schema=_schema({"job_id": {"type": "string"}}, required=["job_id"]),
            handler=_h_render_status,
        ),
        _ToolSpec(
            name="render_wait",
            description=(
                "Block until a render job finishes (or fails / times out) and "
                "return its final status. Prefer this over polling render_status "
                "in a loop."
            ),
            schema=_schema(
                {
                    "job_id": {"type": "string"},
                    "timeout": {
                        "type": "number",
                        "description": "Max seconds to wait. Default 600.",
                    },
                    "poll_interval": {"type": "number", "default": 1.0},
                },
                required=["job_id"],
            ),
            handler=_h_render_wait,
        ),
        _ToolSpec(
            name="render_stop",
            description="Stop the active render.",
            handler=_h_render_stop,
        ),
        _ToolSpec(
            name="render_clear",
            description="Delete every job in the render queue.",
            handler=_h_render_clear,
        ),
        # ---- media -----------------------------------------------------
        _ToolSpec(
            name="media_inspect",
            description="Inspect the current project's media pool (root, current bin, selection).",
            handler=_h_media_inspect,
        ),
        _ToolSpec(
            name="media_bins",
            description="List bins in the current project's media pool.",
            handler=_h_media_bins,
        ),
        _ToolSpec(
            name="media_ls",
            description="List assets in a bin (defaults to the root bin).",
            schema=_schema({"bin": {"type": "string"}}),
            handler=_h_media_ls,
        ),
        _ToolSpec(
            name="media_import",
            description="Import file paths into the media pool.",
            schema=_schema(
                {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "bin": {"type": "string"},
                },
                required=["paths"],
            ),
            handler=_h_media_import,
        ),
        _ToolSpec(
            name="media_scan",
            description=(
                "Scan a filesystem path for importable media files. Returns video/audio "
                "files with relative paths and sizes; skips hidden AppleDouble files by default."
            ),
            schema=_schema(
                {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean", "default": True},
                    "include_hidden": {"type": "boolean", "default": False},
                    "max_files": {"type": "integer", "default": 10000},
                },
                required=["path"],
            ),
            handler=_h_media_scan,
            needs_resolve=False,
        ),
        _ToolSpec(
            name="media_bin_ensure",
            description=("Create a nested media-pool bin path if needed, e.g. `Picture/Plates`."),
            schema=_schema({"path": {"type": "string"}}, required=["path"]),
            handler=_h_media_bin_ensure,
        ),
        _ToolSpec(
            name="media_bin_delete",
            description="Delete a media-pool bin by leaf name or slash path.",
            schema=_schema({"path": {"type": "string"}}, required=["path"]),
            handler=_h_media_bin_delete,
        ),
        _ToolSpec(
            name="media_move",
            description=(
                "Move media-pool clips between bins using safe filters. Moving clips "
                "does not relink or break timeline items."
            ),
            schema=_schema(
                {
                    "source_bin": {"type": "string"},
                    "target_bin": {"type": "string"},
                    "recursive": {"type": "boolean", "default": False},
                    "name_contains": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "description": "Resolve clip Type, e.g. Timeline, Audio, Video + Audio.",
                    },
                    "paths": {"type": "array", "items": {"type": "string"}},
                },
                required=["target_bin"],
            ),
            handler=_h_media_move,
        ),
        # ---- media AI / Studio (Resolve 21+) ---------------------------
        _ToolSpec(
            name="media_transcribe",
            description=(
                "Transcribe audio for a bin (recursively) or a single clip. "
                "speaker_detection (Resolve 21+) overrides the project setting when set."
            ),
            schema=_schema(
                {
                    "bin": {"type": "string"},
                    "clip": {"type": "string"},
                    "speaker_detection": {"type": "boolean"},
                }
            ),
            handler=_h_media_transcribe,
        ),
        _ToolSpec(
            name="media_classify_audio",
            description=(
                "Analyze and classify clip audio into categories for a bin or single clip "
                "(Resolve 21+, Studio). Set clear=true to clear classification instead."
            ),
            schema=_schema(
                {
                    "bin": {"type": "string"},
                    "clip": {"type": "string"},
                    "clear": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_media_classify_audio,
        ),
        _ToolSpec(
            name="media_deblur",
            description=(
                "Apply AI motion deblur to a bin or single clip, rendering new clips "
                "(Resolve 21+, Studio)."
            ),
            schema=_schema(
                {
                    "bin": {"type": "string"},
                    "clip": {"type": "string"},
                    "format": {"type": "string"},
                    "codec": {"type": "string"},
                    "extreme": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_media_deblur,
        ),
        _ToolSpec(
            name="media_analyze",
            description=(
                "Run Intellisearch or AI Slate ID analysis on a bin or single clip "
                "(Resolve 21+, Studio). Returns ok=false if the required Extra is missing."
            ),
            schema=_schema(
                {
                    "kind": {"type": "string", "enum": ["intellisearch", "slate"]},
                    "bin": {"type": "string"},
                    "clip": {"type": "string"},
                    "faces": {"type": "boolean", "default": False},
                    "better": {"type": "boolean", "default": False},
                    "color": {"type": "string", "default": "Blue"},
                },
                required=["kind"],
            ),
            handler=_h_media_analyze,
        ),
        _ToolSpec(
            name="project_reset_intellisearch",
            description="Clear Intellisearch analysis data for the current project (Resolve 21+).",
            handler=_h_project_reset_intellisearch,
        ),
        _ToolSpec(
            name="project_generate_speech",
            description=(
                "Generate a text-to-speech audio clip and optionally place it on the "
                "current timeline (Resolve 21+, Studio)."
            ),
            schema=_schema(
                {
                    "text": {"type": "string"},
                    "voice": {"type": "string", "description": "Voice model, e.g. 'Female 1'."},
                    "speed": {
                        "type": "number",
                        "description": "Speech speed multiplier (1.0 = normal).",
                    },
                    "pitch": {"type": "number", "description": "Voice pitch adjustment."},
                    "filename": {
                        "type": "string",
                        "description": "Name for the generated audio clip.",
                    },
                    "timecode": {"type": "string", "default": "01:00:00:00"},
                    "track": {"type": "integer"},
                    "add_to_timeline": {"type": "boolean", "default": True},
                },
                required=["text"],
            ),
            handler=_h_project_generate_speech,
        ),
        _ToolSpec(
            name="timeline_add_title",
            description=(
                "Insert a (Fusion) title on a timeline and customize its text. Defaults "
                "to the built-in 'Text+'. Supports text, font, style, size, color "
                "(hex/name/[r,g,b]), opacity, tracking, line_spacing, pos_x/pos_y, and "
                "align/vertical_align. Seeks to `timecode` first when given."
            ),
            schema=_schema(
                {
                    "title": {"type": "string", "default": "Text+"},
                    "fusion": {"type": "boolean", "default": True},
                    "text": {"type": "string"},
                    "font": {"type": "string"},
                    "style": {"type": "string", "description": "Regular, Bold, Italic, ..."},
                    "size": {"type": "number", "description": "Relative size, ~0.05-0.2."},
                    "color": {
                        "description": "Hex '#ffcc00', a name like 'white', or [r,g,b] floats.",
                    },
                    "opacity": {"type": "number", "description": "Text alpha, 0..1."},
                    "tracking": {"type": "number", "description": "Letter spacing."},
                    "line_spacing": {"type": "number"},
                    "pos_x": {"type": "number", "description": "Layout center X, 0..1."},
                    "pos_y": {"type": "number", "description": "Layout center Y, 0..1."},
                    "align": {
                        "type": "string",
                        "enum": ["left", "center", "right"],
                    },
                    "vertical_align": {
                        "type": "string",
                        "enum": ["top", "center", "bottom"],
                    },
                    "timecode": {"type": "string", "description": "Seek here before inserting."},
                    "timeline": {"type": "string"},
                }
            ),
            handler=_h_timeline_add_title,
        ),
        _ToolSpec(
            name="clip_set_text",
            description=(
                "Customize Text+ content/styling on timeline items selected by safe "
                "filters. Only clips carrying a Fusion Text+ tool are updated; others "
                "are reported as skipped. Same styling fields as timeline_add_title."
            ),
            schema=_schema(
                {
                    "text": {"type": "string"},
                    "font": {"type": "string"},
                    "style": {"type": "string"},
                    "size": {"type": "number"},
                    "color": {"description": "Hex '#ffcc00', a name, or [r,g,b] floats."},
                    "opacity": {"type": "number"},
                    "tracking": {"type": "number"},
                    "line_spacing": {"type": "number"},
                    "pos_x": {"type": "number"},
                    "pos_y": {"type": "number"},
                    "align": {"type": "string", "enum": ["left", "center", "right"]},
                    "vertical_align": {"type": "string", "enum": ["top", "center", "bottom"]},
                    "timeline": {"type": "string"},
                    "track_type": {
                        "type": "string",
                        "enum": ["video", "audio", "subtitle"],
                        "default": "video",
                    },
                    "track_index": {"type": "integer"},
                    "name": {"type": "string"},
                    "name_contains": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                }
            ),
            handler=_h_clip_set_text,
        ),
        _ToolSpec(
            name="timeline_create_subtitles",
            description=(
                "Generate subtitles from a timeline's audio using Resolve's Whisper "
                "engine (Studio). Supports language, chars_per_line, line_break_type, "
                "and an optional caption preset."
            ),
            schema=_schema(
                {
                    "language": {"type": "string", "default": "auto"},
                    "chars_per_line": {"type": "integer", "default": 42},
                    "line_break_type": {"type": "string", "default": "Auto"},
                    "preset": {"type": "string"},
                    "timeline": {"type": "string"},
                }
            ),
            handler=_h_timeline_create_subtitles,
        ),
        _ToolSpec(
            name="timeline_set_start_timecode",
            description="Set a timeline's start timecode (e.g. '01:00:00:00').",
            schema=_schema(
                {"timecode": {"type": "string"}, "timeline": {"type": "string"}},
                required=["timecode"],
            ),
            handler=_h_timeline_set_start_timecode,
        ),
        _ToolSpec(
            name="timeline_add_generator",
            description=(
                "Insert a generator at the playhead. Set fusion=true for a Fusion "
                "generator or ofx=true for an OFX generator; otherwise a standard one. "
                "Seeks to `timecode` first when given."
            ),
            schema=_schema(
                {
                    "name": {"type": "string"},
                    "fusion": {"type": "boolean", "default": False},
                    "ofx": {"type": "boolean", "default": False},
                    "timecode": {"type": "string"},
                    "timeline": {"type": "string"},
                },
                required=["name"],
            ),
            handler=_h_timeline_add_generator,
        ),
        _ToolSpec(
            name="timeline_grab_stills",
            description="Grab a still from every clip into the gallery (source: first|middle).",
            schema=_schema(
                {
                    "source": {"type": "string", "enum": ["first", "middle"], "default": "first"},
                    "timeline": {"type": "string"},
                }
            ),
            handler=_h_timeline_grab_stills,
        ),
        _ToolSpec(
            name="timeline_import_into",
            description="Import items from an AAF/timeline file into a timeline.",
            schema=_schema(
                {
                    "file_path": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": True},
                    "timeline": {"type": "string"},
                },
                required=["file_path"],
            ),
            handler=_h_timeline_import_into,
        ),
        _ToolSpec(
            name="render_mode",
            description="Get or set render mode. Pass set='individual' or set='single'.",
            schema=_schema({"set": {"type": "string", "enum": ["individual", "single"]}}),
            handler=_h_render_mode,
        ),
        _ToolSpec(
            name="render_resolutions",
            description="List valid render resolutions for a format/codec (or all).",
            schema=_schema({"format": {"type": "string"}, "codec": {"type": "string"}}),
            handler=_h_render_resolutions,
        ),
        _ToolSpec(
            name="render_refresh_luts",
            description="Refresh Resolve's LUT list so newly-added LUTs become settable.",
            handler=_h_render_refresh_luts,
        ),
        _ToolSpec(
            name="project_color_groups",
            description="List color groups, or create one with add=NAME / delete one with delete=NAME.",
            schema=_schema({"add": {"type": "string"}, "delete": {"type": "string"}}),
            handler=_h_project_color_groups,
        ),
        _ToolSpec(
            name="project_export_still",
            description="Export the current Color-page frame as a still image (Resolve 18.5+).",
            schema=_schema({"file_path": {"type": "string"}}, required=["file_path"]),
            handler=_h_project_export_still,
        ),
        _ToolSpec(
            name="media_export_metadata",
            description="Export metadata for every media-pool clip to a CSV file.",
            schema=_schema({"file_path": {"type": "string"}}, required=["file_path"]),
            handler=_h_media_export_metadata,
        ),
        _ToolSpec(
            name="media_import_bin",
            description="Import a media-pool bin from a .drb file (Resolve 18+).",
            schema=_schema(
                {"file_path": {"type": "string"}, "source_clips": {"type": "string"}},
                required=["file_path"],
            ),
            handler=_h_media_import_bin,
        ),
        _ToolSpec(
            name="disable_background_tasks",
            description=(
                "Disable background tasks for the current Resolve session (Resolve 21+; "
                "no-op on older builds). Useful before scripted renders."
            ),
            handler=_h_disable_background_tasks,
        ),
        _ToolSpec(
            name="timeline_assemble",
            description=(
                "Workflow tool: assemble a rough cut in one call. Ensures the "
                "timeline exists, imports any media given by path (into an "
                "optional bin), and appends every item in order. Use "
                "timeline_append instead when you need per-item track targeting."
            ),
            schema=_schema(
                {
                    "timeline": {"type": "string"},
                    "fps": {"type": "number", "description": "Optional timeline frame rate."},
                    "bin": {
                        "type": "string",
                        "description": "Bin path (e.g. 'Footage/Day01') to import into.",
                    },
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "File path to import (or reuse if imported).",
                                },
                                "name": {
                                    "type": "string",
                                    "description": "Existing media-pool clip name (alternative to path).",
                                },
                                "start_frame": {"type": "integer"},
                                "end_frame": {"type": "integer"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                required=["timeline", "items"],
            ),
            handler=_h_timeline_assemble,
        ),
        _ToolSpec(
            name="timeline_append",
            description=(
                "Append media to a timeline with explicit track targeting. Supports "
                "path/name lookup, media_type video/audio, track_index, record_frame, "
                "and optional source start/end frames."
            ),
            schema=_schema(
                {
                    "timeline": {"type": "string"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "name": {"type": "string"},
                                "bin": {"type": "string"},
                                "media_type": {"type": "string", "enum": ["video", "audio"]},
                                "track_index": {
                                    "type": "integer",
                                    "description": (
                                        "1-based Resolve track index. Values >= 2 require "
                                        "an explicit record_frame for each item."
                                    ),
                                },
                                "record_frame": {
                                    "type": "integer",
                                    "description": (
                                        "Timeline record frame. Required for non-default "
                                        "track targets (track_index >= 2)."
                                    ),
                                },
                                "start_frame": {"type": "integer"},
                                "end_frame": {"type": "integer"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                required=["items"],
            ),
            handler=_h_timeline_append,
        ),
        _ToolSpec(
            name="interchange_export",
            description=(
                "Export the current timeline to an interchange format "
                "(EDL, AAF, FCPXML, OTIO, etc.)."
            ),
            schema=_schema(
                {
                    "file_path": {"type": "string"},
                    "format": {
                        "type": "string",
                        "description": (
                            "One of: aaf, edl, edl-cdl, fcpxml-1.10, drt, otio, ale, etc."
                        ),
                    },
                },
                required=["file_path"],
            ),
            handler=_h_interchange_export,
        ),
        # ---- diff / spec ----------------------------------------------
        _ToolSpec(
            name="diff_timelines",
            description=(
                "Structured diff between two timelines in the current project. "
                "Lists align by name/id/frame so reordering doesn't produce noise."
            ),
            schema=_schema(
                {"a": {"type": "string"}, "b": {"type": "string"}},
                required=["a", "b"],
            ),
            handler=_h_diff_timelines,
        ),
        _ToolSpec(
            name="diff_to_spec",
            description="Diff the live Resolve state against a spec (YAML/JSON file path).",
            schema=_schema({"spec_path": {"type": "string"}}, required=["spec_path"]),
            handler=_h_diff_to_spec,
        ),
        _ToolSpec(
            name="apply_spec",
            description=(
                "Reconcile the live Resolve state to match a declarative spec "
                "(YAML/JSON file path). Returns the list of actions taken (or "
                "would be taken, when dry_run=true). Set transactional=true to "
                "snapshot first and auto-rollback on failure; verify=true to "
                "read every setting back after writing."
            ),
            schema=_schema(
                {
                    "spec_path": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                    "run_hooks": {"type": "boolean", "default": True},
                    "continue_on_error": {"type": "boolean", "default": False},
                    "transactional": {"type": "boolean", "default": False},
                    "verify": {"type": "boolean", "default": False},
                },
                required=["spec_path"],
            ),
            handler=_h_apply_spec,
        ),
        _ToolSpec(
            name="spec_export",
            description=(
                "Build a declarative spec from live project state (the inverse "
                "of apply_spec) — settings, bins, timelines, tracks, markers. "
                "Use it to adopt an existing project into spec-managed workflows."
            ),
            schema=_schema(
                {
                    "project": {
                        "type": "string",
                        "description": "Project to export. Default: the current project.",
                    }
                }
            ),
            handler=_h_spec_export,
        ),
        # ---- snapshot --------------------------------------------------
        _ToolSpec(
            name="snapshot_save",
            description=(
                "Capture the current project state to a snapshot on disk. "
                "Returns the snapshot name and path."
            ),
            schema=_schema(
                {
                    "name": {
                        "type": "string",
                        "description": ("Snapshot name. Default: '<project>@<UTC timestamp>'."),
                    }
                }
            ),
            handler=_h_snapshot_save,
        ),
        _ToolSpec(
            name="snapshot_restore",
            description="Re-apply a snapshot to the live Resolve state.",
            schema=_schema(
                {
                    "name": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                required=["name"],
            ),
            handler=_h_snapshot_restore,
        ),
        # ---- lint ------------------------------------------------------
        _ToolSpec(
            name="lint",
            description=(
                "Pre-flight validation of the current project / timeline / render "
                "config. Returns structured error/warning/info issues."
            ),
            handler=_h_lint,
        ),
        # ---- power-user (eval, gated) ---------------------------------
        _ToolSpec(
            name="eval",
            description=(
                "Evaluate a Python expression with `r = Resolve()` already bound. "
                "Disabled unless DVR_MCP_ENABLE_EVAL=1 is set in the server's env. "
                "Only `r`, `project`, `timeline`, and `dvr` are in scope. No imports."
            ),
            schema=_schema({"expression": {"type": "string"}}, required=["expression"]),
            handler=_h_eval,
        ),
    ]


# ---------------------------------------------------------------------------
# Resources — live state agents can *read* instead of guessing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResourceSpec:
    """One MCP resource: a URI backed by a JSON-producing reader."""

    uri: str
    name: str
    description: str
    handler: Callable[[_Context], Any]
    needs_resolve: bool = True


def _build_resource_registry() -> list[_ResourceSpec]:
    from .. import schema as schema_mod

    def _schema_reader(topic: str) -> Callable[[_Context], Any]:
        return lambda _ctx: schema_mod.get_topic(topic)

    resources = [
        _ResourceSpec(
            uri="dvr://inspect",
            name="Resolve state",
            description="One-call snapshot of Resolve, current project, and current timeline.",
            handler=lambda ctx: ctx.resolve().inspect(),
        ),
        _ResourceSpec(
            uri="dvr://project/current",
            name="Current project",
            description="Inspect the currently loaded project (timelines, counts).",
            handler=lambda ctx: _current_project(ctx).inspect(),
        ),
        _ResourceSpec(
            uri="dvr://timeline/current",
            name="Current timeline",
            description="Full inspect of the current timeline: tracks, items, markers.",
            handler=lambda ctx: _read_current_timeline(ctx),
        ),
        _ResourceSpec(
            uri="dvr://media/bins",
            name="Media pool bins",
            description="The current project's bin tree.",
            handler=lambda ctx: [b.inspect() for b in _current_project(ctx).media.root.subbins()],
        ),
        _ResourceSpec(
            uri="dvr://render/queue",
            name="Render queue",
            description="Jobs currently in the render queue.",
            handler=lambda ctx: list(ctx.resolve().render.queue()),
        ),
        _ResourceSpec(
            uri="dvr://doctor",
            name="Setup diagnostics",
            description="Static dvr <-> Resolve environment diagnosis (no connection attempt).",
            handler=lambda ctx: _h_doctor(ctx, {}),
            needs_resolve=False,
        ),
    ]
    for topic in ("clip-properties", "settings", "color-presets", "export-formats"):
        resources.append(
            _ResourceSpec(
                uri=f"dvr://schema/{topic}",
                name=f"Schema: {topic}",
                description=f"Catalog of known-good values for {topic}.",
                handler=_schema_reader(topic),
                needs_resolve=False,
            )
        )
    return resources


def _read_current_timeline(ctx: _Context) -> dict[str, Any]:
    tl = ctx.resolve().timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")
    return tl.inspect()


def list_resource_specs() -> list[_ResourceSpec]:
    """Return the resource registry. Public so tests / CLI can introspect."""
    return _build_resource_registry()


# ---------------------------------------------------------------------------
# Tool dispatch helpers
# ---------------------------------------------------------------------------


def _serialize(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def _ok(value: Any) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=_serialize(value))])


def _err(exc: errors.DvrError | Exception) -> CallToolResult:
    if isinstance(exc, errors.DvrError):
        payload = {"error": exc.to_dict()}
    else:
        payload = {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "cause": None,
                "fix": None,
                "state": {},
            }
        }
    return CallToolResult(
        content=[TextContent(type="text", text=_serialize(payload))], isError=True
    )


def _dispatch(
    registry: dict[str, _ToolSpec],
    cache: _ResolveCache,
    name: str,
    args: dict[str, Any],
) -> CallToolResult:
    spec = registry.get(name)
    if spec is None:
        return _err(errors.DvrError(f"Unknown tool: {name!r}"))

    ctx = _Context(cache=cache)
    try:
        value = spec.handler(ctx, args or {})
    except errors.DvrError as exc:
        return _err(exc)
    except Exception as exc:  # boundary
        logger.exception("tool %r raised", name)
        return _err(errors.DvrError(f"{type(exc).__name__}: {exc}"))
    return _ok(value)


def list_tool_specs() -> list[_ToolSpec]:
    """Return the registry as a list. Public so tests / CLI can introspect."""
    return _build_registry()


def list_tools_metadata() -> list[dict[str, Any]]:
    """Return tools as plain dicts for CLI / docs introspection."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "needs_resolve": s.needs_resolve,
            "input_schema": s.schema,
        }
        for s in _build_registry()
    ]


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def build_server(*, auto_launch: bool = True, timeout: float = 30.0) -> Server:
    """Construct an MCP Server with all `dvr` tools and resources registered."""
    server = Server("dvr")
    cache = _ResolveCache(auto_launch=auto_launch, timeout=timeout)
    specs = _build_registry()
    registry = {s.name: s for s in specs}
    tools = [Tool(name=s.name, description=s.description, inputSchema=s.schema) for s in specs]

    resource_specs = _build_resource_registry()
    resource_registry = {r.uri: r for r in resource_specs}
    resources = [
        Resource(
            uri=r.uri,  # type: ignore[arg-type]
            name=r.name,
            description=r.description,
            mimeType="application/json",
        )
        for r in resource_specs
    ]

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        return _dispatch(registry, cache, name, arguments or {})

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        return resources

    @server.read_resource()
    async def _read_resource(uri: Any) -> str:
        spec = resource_registry.get(str(uri))
        if spec is None:
            raise errors.DvrError(
                f"Unknown resource: {uri}",
                fix=f"Available: {', '.join(resource_registry)}",
            )
        return _serialize(spec.handler(_Context(cache=cache)))

    return server


async def _run_async(*, auto_launch: bool, timeout: float) -> None:
    server = build_server(auto_launch=auto_launch, timeout=timeout)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_stdio(*, auto_launch: bool = True, timeout: float = 30.0) -> None:
    """Run the MCP server over stdio. Blocks until stdin closes."""
    import asyncio

    asyncio.run(_run_async(auto_launch=auto_launch, timeout=timeout))


__all__ = [
    "build_server",
    "list_resource_specs",
    "list_tool_specs",
    "list_tools_metadata",
    "run_stdio",
]
