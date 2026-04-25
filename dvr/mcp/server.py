"""MCP server implementation.

Each tool is a thin wrapper around a single library method. Tools are
declared with explicit JSON schemas so MCP clients (Claude, Cursor,
others) can show the LLM exactly what arguments are accepted and what
shape the response will take.

Errors come back as :class:`dvr.errors.DvrError.to_dict` payloads
inside the tool's text content — this lets the LLM read the
``cause`` / ``fix`` / ``state`` fields and recover.
"""

from __future__ import annotations

import json
from typing import Any

from .. import errors
from ..resolve import Resolve

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'The MCP server requires the optional `mcp` extra. Install with `pip install "dvr[mcp]"`.'
    ) from exc


# ---------------------------------------------------------------------------
# Connection cache (one per server lifetime)
# ---------------------------------------------------------------------------


class _ResolveCache:
    """Lazily connect on first tool call; reuse for the rest of the session."""

    def __init__(self, *, auto_launch: bool, timeout: float) -> None:
        self._auto_launch = auto_launch
        self._timeout = timeout
        self._resolve: Resolve | None = None

    def get(self) -> Resolve:
        if self._resolve is None:
            self._resolve = Resolve(auto_launch=self._auto_launch, timeout=self._timeout)
        return self._resolve


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def _tool(name: str, description: str, schema: dict[str, Any] | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
    )


def _tools() -> list[Tool]:
    return [
        _tool("ping", "Verify the connection to DaVinci Resolve. Returns version info."),
        _tool(
            "inspect",
            "Snapshot of the app, current project, and current timeline in one call. "
            "This is the most efficient way to read DaVinci's state before making decisions.",
        ),
        _tool(
            "page_set",
            "Switch to a Resolve page (media, cut, edit, fusion, color, fairlight, deliver).",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ),
        _tool("project_list", "List projects in the current Project Manager folder."),
        _tool(
            "project_ensure",
            "Load a project by name, creating it if it does not exist. Idempotent.",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ),
        _tool("project_current", "Inspect the currently loaded project."),
        _tool(
            "project_save",
            "Save the currently loaded project.",
        ),
        _tool("timeline_list", "List timelines in the currently loaded project."),
        _tool(
            "timeline_inspect",
            "Return a structured snapshot of a timeline (tracks, clips, markers).",
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Timeline name. Defaults to the current timeline.",
                    }
                },
            },
        ),
        _tool(
            "timeline_ensure",
            "Get-or-create a timeline by name in the current project. Idempotent.",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ),
        _tool(
            "timeline_switch",
            "Set a timeline as the current one.",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ),
        _tool("render_queue", "List jobs in the render queue."),
        _tool("render_presets", "List available render presets."),
        _tool("render_formats", "List render container formats."),
        _tool(
            "render_codecs",
            "List codecs available for a container format.",
            {
                "type": "object",
                "properties": {"format": {"type": "string"}},
                "required": ["format"],
            },
        ),
        _tool(
            "render_submit",
            "Configure and queue a render of the current timeline. Returns a job_id.",
            {
                "type": "object",
                "properties": {
                    "target_dir": {"type": "string"},
                    "custom_name": {"type": "string"},
                    "preset": {"type": "string"},
                    "format": {"type": "string"},
                    "codec": {"type": "string"},
                    "start": {"type": "boolean", "default": True},
                },
                "required": ["target_dir"],
            },
        ),
        _tool(
            "render_status",
            "Get the status of a render job.",
            {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        _tool("render_stop", "Stop the active render."),
        _tool(
            "media_inspect",
            "Inspect the current project's media pool (root bin, current bin, selection).",
        ),
        _tool(
            "media_bins",
            "List bins in the current project's media pool.",
        ),
        _tool(
            "media_ls",
            "List assets in a bin (defaults to the root bin).",
            {
                "type": "object",
                "properties": {"bin": {"type": "string"}},
            },
        ),
        _tool(
            "media_import",
            "Import file paths into the media pool.",
            {
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "bin": {"type": "string"},
                },
                "required": ["paths"],
            },
        ),
        _tool(
            "interchange_export",
            "Export the current timeline to an interchange format (EDL, AAF, FCPXML, OTIO, etc.).",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "format": {
                        "type": "string",
                        "description": "One of: aaf, edl, edl-cdl, fcpxml-1.10, drt, otio, ale, etc.",
                    },
                },
                "required": ["file_path"],
            },
        ),
        # --- diff -------------------------------------------------------
        _tool(
            "diff_timelines",
            "Structured diff between two timelines in the current project. "
            "Lists align by name/id/frame so reordering doesn't produce noise.",
            {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                },
                "required": ["a", "b"],
            },
        ),
        _tool(
            "diff_to_spec",
            "Diff the live Resolve state against a spec (YAML/JSON file path).",
            {
                "type": "object",
                "properties": {"spec_path": {"type": "string"}},
                "required": ["spec_path"],
            },
        ),
        # --- snapshot ---------------------------------------------------
        _tool(
            "snapshot_save",
            "Capture the current project state to a snapshot on disk. "
            "Returns the snapshot name and path.",
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Snapshot name. Default: '<project>@<UTC timestamp>'.",
                    }
                },
            },
        ),
        _tool("snapshot_list", "List snapshots on disk, newest first."),
        _tool(
            "snapshot_restore",
            "Re-apply a snapshot to the live Resolve state.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["name"],
            },
        ),
        # --- lint -------------------------------------------------------
        _tool(
            "lint",
            "Pre-flight validation of the current project / timeline / render config. "
            "Returns structured error/warning/info issues.",
        ),
        # --- schema -----------------------------------------------------
        _tool(
            "schema",
            "Discoverable catalog of valid setting keys, codecs, properties. "
            "Topic is one of: clip-properties, settings, export-formats, "
            "color-presets, render-formats, render-codecs, render-presets.",
            {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        ),
        # --- power-user -------------------------------------------------
        _tool(
            "eval",
            "Evaluate a Python expression with `r = Resolve()` already bound. "
            "Use sparingly: only the `r` (Resolve), `project`, `timeline` and "
            "`dvr` (the package) names are in scope. No imports.",
            {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        ),
        # --- color ------------------------------------------------------
        _tool(
            "page_get",
            "Read the current Resolve page name (media|cut|edit|fusion|color|fairlight|deliver).",
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def _ok(value: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(value, indent=2, default=str))]


def _err(exc: errors.DvrError) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": exc.to_dict()}, indent=2))]


def _dispatch(cache: _ResolveCache, name: str, args: dict[str, Any]) -> list[TextContent]:
    try:
        r = cache.get()

        if name == "ping":
            return _ok({"connected": True, "version": r.app.version, "product": r.app.product})

        if name == "inspect":
            return _ok(r.inspect())

        if name == "page_set":
            r.app.page = args["name"]
            return _ok({"page": r.app.page})

        if name == "project_list":
            return _ok([{"name": n} for n in r.project.list()])

        if name == "project_ensure":
            return _ok(r.project.ensure(args["name"]).inspect())

        if name == "project_current":
            current = r.project.current
            return _ok(current.inspect() if current else {"current": None})

        if name == "project_save":
            current = r.project.current
            if current is None:
                raise errors.ProjectError("No project is currently loaded.")
            current.save()
            return _ok({"saved": current.name})

        if name == "timeline_list":
            return _ok(
                [
                    {"name": tl.name, "fps": tl.fps, "duration": tl.duration_frames}
                    for tl in r.timeline.list()
                ]
            )

        if name == "timeline_inspect":
            requested = args.get("name")
            tl = r.timeline.get(requested) if requested else r.timeline.current
            if tl is None:
                raise errors.TimelineError("No timeline is currently loaded.")
            return _ok(tl.inspect())

        if name == "timeline_ensure":
            return _ok(r.timeline.ensure(args["name"]).inspect())

        if name == "timeline_switch":
            tl = r.timeline.set_current(args["name"])
            return _ok({"current": tl.name})

        if name == "render_queue":
            return _ok(r.render.queue())

        if name == "render_presets":
            return _ok([{"name": n} for n in r.render.presets()])

        if name == "render_formats":
            return _ok([{"format": k, "extension": v} for k, v in r.render.formats().items()])

        if name == "render_codecs":
            return _ok(
                [{"codec": k, "label": v} for k, v in r.render.codecs(args["format"]).items()]
            )

        if name == "render_submit":
            job = r.render.submit(
                target_dir=args["target_dir"],
                custom_name=args.get("custom_name"),
                preset=args.get("preset"),
                format=args.get("format"),
                codec=args.get("codec"),
                start=bool(args.get("start", True)),
            )
            return _ok({"job_id": job.id, "started": bool(args.get("start", True))})

        if name == "render_status":
            from ..render import RenderJob

            job = RenderJob(r.render, args["job_id"])
            return _ok(job.inspect())

        if name == "render_stop":
            r.render.stop()
            return _ok({"stopped": True})

        if name == "media_inspect":
            current = r.project.current
            if current is None:
                raise errors.ProjectError("No project is currently loaded.")
            return _ok(current.media.inspect())

        if name == "media_bins":
            current = r.project.current
            if current is None:
                raise errors.ProjectError("No project is currently loaded.")
            return _ok([b.inspect() for b in current.media.root.subbins()])

        if name == "media_ls":
            current = r.project.current
            if current is None:
                raise errors.ProjectError("No project is currently loaded.")
            target = current.media._find_bin(args["bin"]) if args.get("bin") else current.media.root
            return _ok([a.inspect() for a in target.assets()])

        if name == "media_import":
            current = r.project.current
            if current is None:
                raise errors.ProjectError("No project is currently loaded.")
            target_bin = current.media._find_bin(args["bin"]) if args.get("bin") else None
            assets = current.media.import_(args["paths"], bin=target_bin)
            return _ok([a.inspect() for a in assets])

        if name == "interchange_export":
            from .. import interchange

            tl = r.timeline.current
            if tl is None:
                raise errors.TimelineError("No timeline is currently loaded.")
            path = interchange.export(
                tl,
                args["file_path"],
                format=args.get("format", "fcpxml-1.10"),
            )
            return _ok({"exported": path})

        # --- diff ---
        if name == "diff_timelines":
            from .. import diff

            left = r.timeline.get(args["a"])
            right = r.timeline.get(args["b"])
            return _ok(diff.compare_timelines(left, right).to_dict())

        if name == "diff_to_spec":
            from .. import diff
            from .. import spec as spec_mod

            parsed = spec_mod.load_spec(args["spec_path"])
            return _ok(diff.compare_to_spec(r, parsed).to_dict())

        # --- snapshot ---
        if name == "snapshot_save":
            from .. import snapshot as snap_mod

            snap = snap_mod.capture(r, name=args.get("name") or None)
            snap_path = snap_mod.save(snap)
            return _ok(
                {
                    "name": snap.name,
                    "project": snap.project,
                    "captured_at": snap.captured_at,
                    "path": str(snap_path),
                }
            )

        if name == "snapshot_list":
            from .. import snapshot as snap_mod

            return _ok(
                [
                    {"name": s.name, "project": s.project, "captured_at": s.captured_at}
                    for s in snap_mod.list_snapshots()
                ]
            )

        if name == "snapshot_restore":
            from .. import snapshot as snap_mod

            snap = snap_mod.load(args["name"])
            counts = snap_mod.restore(r, snap, dry_run=bool(args.get("dry_run", False)))
            return _ok({"snapshot": snap.name, "project": snap.project, **counts})

        # --- lint ---
        if name == "lint":
            from .. import lint as lint_mod

            return _ok(lint_mod.lint(r).to_dict())

        # --- schema ---
        if name == "schema":
            from .. import schema as schema_mod

            topic = args["topic"]
            data = (
                schema_mod.get_topic(topic, r)
                if topic in ("render-formats", "render-codecs", "render-presets")
                else schema_mod.get_topic(topic)
            )
            return _ok(data)

        # --- eval ---
        if name == "eval":
            import dvr as _dvr

            project = r.project.current
            timeline = project.timeline.current if project else None
            ns = {"r": r, "project": project, "timeline": timeline, "dvr": _dvr}
            value = eval(args["expression"], ns)
            if hasattr(value, "inspect") and callable(value.inspect):
                value = value.inspect()
            elif hasattr(value, "to_dict") and callable(value.to_dict):
                value = value.to_dict()
            return _ok(value)

        # --- page_get ---
        if name == "page_get":
            return _ok({"page": r.app.page})

        raise errors.DvrError(f"Unknown tool: {name!r}")

    except errors.DvrError as exc:
        return _err(exc)
    except Exception as exc:
        return _err(errors.DvrError(f"{type(exc).__name__}: {exc}"))


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def build_server(*, auto_launch: bool = True, timeout: float = 30.0) -> Server:
    """Construct an MCP Server with all `dvr` tools registered."""
    server = Server("dvr")
    cache = _ResolveCache(auto_launch=auto_launch, timeout=timeout)
    tools = _tools()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        return _dispatch(cache, name, arguments or {})

    return server


async def _run_async(*, auto_launch: bool, timeout: float) -> None:
    server = build_server(auto_launch=auto_launch, timeout=timeout)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_stdio(*, auto_launch: bool = True, timeout: float = 30.0) -> None:
    """Run the MCP server over stdio. Blocks until stdin closes."""
    import asyncio

    asyncio.run(_run_async(auto_launch=auto_launch, timeout=timeout))


__all__ = ["build_server", "run_stdio"]
