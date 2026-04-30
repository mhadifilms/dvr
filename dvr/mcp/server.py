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
from ..resolve import Resolve

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, TextContent, Tool
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


_VIDEO_EXTS = {".mov", ".mp4", ".mxf", ".avi", ".mkv", ".exr", ".dpx", ".tif", ".tiff"}
_AUDIO_EXTS = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac"}


def _skip_fs_name(name: str, *, include_hidden: bool = False) -> bool:
    if include_hidden:
        return False
    return name.startswith(".") or name.startswith("._") or name == ".DS_Store"


def _media_kind_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "other"


def _walk_media_files(
    root: str,
    *,
    recursive: bool = True,
    include_hidden: bool = False,
    max_files: int = 10000,
) -> list[dict[str, Any]]:
    base = Path(root).expanduser()
    if not base.exists():
        raise errors.MediaError(
            f"Media path does not exist: {root}",
            fix="Pass an absolute path visible to the Resolve machine.",
            state={"path": root},
        )
    if base.is_file():
        candidates = [base]
    elif recursive:
        candidates = [p for p in base.rglob("*") if p.is_file()]
    else:
        candidates = [p for p in base.iterdir() if p.is_file()]

    files: list[dict[str, Any]] = []
    for p in sorted(candidates):
        rel_parts = p.relative_to(base).parts if base.is_dir() else (p.name,)
        if any(_skip_fs_name(part, include_hidden=include_hidden) for part in rel_parts):
            continue
        kind = _media_kind_for_path(str(p))
        if kind == "other":
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        files.append(
            {
                "path": str(p),
                "relative_path": str(Path(*rel_parts)),
                "name": p.name,
                "extension": p.suffix.lower(),
                "kind": kind,
                "size": size,
            }
        )
        if len(files) >= max_files:
            break
    return files


def _current_project(ctx: _Context) -> Any:
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
    return current


def _ensure_bin_path(media: Any, path: str | list[str]) -> Any:
    parts = [p for p in (path if isinstance(path, list) else str(path).split("/")) if p]
    current = media.root
    for part in parts:
        current = media.ensure_folder(str(part), parent=current)
    return current


def _find_bin_path(media: Any, path: str | list[str]) -> Any:
    parts = [p for p in (path if isinstance(path, list) else str(path).split("/")) if p]
    if not parts:
        return media.root
    if len(parts) == 1:
        return media._find_folder(parts[0])
    current = media.root
    for part in parts:
        for sub in current.subfolders:
            if sub.name == part:
                current = sub
                break
        else:
            raise errors.MediaError(
                f"No folder at path {path!r}.",
                fix="Create it with `media_bin_ensure` first, or pass an existing bin path.",
                state={"requested": path, "missing": part},
            )
    return current


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
    from ..connection import _platform_paths, _resolve_running

    api_dir, lib_path = _platform_paths()
    out: dict[str, Any] = {
        "dvr_version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "brand": _brand_metadata(),
        "scripting_api_dir": api_dir,
        "scripting_lib_path": lib_path,
        "scripting_lib_present": os.path.exists(lib_path),
        "resolve_process_running": _resolve_running(),
        "env": {
            "RESOLVE_SCRIPT_API": os.environ.get("RESOLVE_SCRIPT_API"),
            "RESOLVE_SCRIPT_LIB": os.environ.get("RESOLVE_SCRIPT_LIB"),
        },
        # Whether the long-lived MCP cache already has a live connection or
        # is in the failure-cooldown window from a recent failed connect.
        "connection_cached": ctx.cache._resolve is not None,
        "last_connection_error": (
            ctx.cache._error.to_dict() if ctx.cache._error is not None else None
        ),
    }
    if not bool(args.get("probe", False)):
        return out

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
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
    keys = args.get("keys")
    if keys:
        return {str(key): current.get_setting(str(key)) for key in keys}
    settings = current.get_setting()
    return dict(settings) if isinstance(settings, dict) else {"settings": settings}


def _h_project_save(ctx: _Context, _args: dict[str, Any]) -> dict[str, Any]:
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
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


def _h_clip_where(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter clips on a timeline using safe predicate fields.

    The MCP boundary deliberately does *not* expose a Python lambda --
    instead, callers pick from a small, declarative DSL of safe filters.
    """
    r = ctx.resolve()
    name = args.get("timeline")
    tl = r.timeline.get(name) if name else r.timeline.current
    if tl is None:
        raise errors.TimelineError("No timeline is currently loaded.")

    track_type: str = args.get("track_type", "video")
    duration_lt = args.get("duration_lt")
    duration_gt = args.get("duration_gt")
    name_contains = args.get("name_contains")

    items: list[Any] = []
    for tr in tl.tracks(track_type):
        items.extend(tr.items)

    def matches(item: Any) -> bool:
        if duration_lt is not None and not item.duration < int(duration_lt):
            return False
        if duration_gt is not None and not item.duration > int(duration_gt):
            return False
        return not (name_contains is not None and name_contains not in (item.name or ""))

    matched = [it for it in items if matches(it)]
    return [
        {
            "name": it.name,
            "track_type": track_type,
            "duration_frames": it.duration,
            "start_frame": it.start,
            "end_frame": it.end,
        }
        for it in matched
    ]


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
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
    return current.media.inspect()


def _h_media_bins(ctx: _Context, _args: dict[str, Any]) -> list[dict[str, Any]]:
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
    return [b.inspect() for b in current.media.root.subbins()]


def _h_media_ls(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
    target = _find_bin_path(current.media, args["bin"]) if args.get("bin") else current.media.root
    return [a.inspect() for a in target.assets()]


def _h_media_import(ctx: _Context, args: dict[str, Any]) -> list[dict[str, Any]]:
    current = ctx.resolve().project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
    target_bin = _ensure_bin_path(current.media, args["bin"]) if args.get("bin") else None
    assets = current.media.import_(args["paths"], bin=target_bin)
    return [a.inspect() for a in assets]


def _h_media_scan(_ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    files = _walk_media_files(
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


def _h_timeline_append(ctx: _Context, args: dict[str, Any]) -> dict[str, Any]:
    r = ctx.resolve()
    current = r.project.current
    if current is None:
        raise errors.ProjectError("No project is currently loaded.")
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
                "Topics: clip-properties, settings, export-formats, color-presets, "
                "render-formats, render-codecs, render-presets. Live topics "
                "(render-*) require an active Resolve connection; static topics do not."
            ),
            schema=_schema(
                {
                    "topic": {
                        "type": "string",
                        "enum": [
                            "clip-properties",
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
                "would be taken, when dry_run=true)."
            ),
            schema=_schema(
                {
                    "spec_path": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                    "run_hooks": {"type": "boolean", "default": True},
                    "continue_on_error": {"type": "boolean", "default": False},
                },
                required=["spec_path"],
            ),
            handler=_h_apply_spec,
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
    """Construct an MCP Server with all `dvr` tools registered."""
    server = Server("dvr")
    cache = _ResolveCache(auto_launch=auto_launch, timeout=timeout)
    specs = _build_registry()
    registry = {s.name: s for s in specs}
    tools = [Tool(name=s.name, description=s.description, inputSchema=s.schema) for s in specs]

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        return _dispatch(registry, cache, name, arguments or {})

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
    "list_tool_specs",
    "list_tools_metadata",
    "run_stdio",
]
