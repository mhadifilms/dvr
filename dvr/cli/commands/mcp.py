"""``dvr mcp`` sub-commands -- MCP server for LLM agents."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from .. import output

app = typer.Typer(name="mcp", help="MCP server: expose dvr to LLM agents.")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@app.command("serve")
def serve(ctx: typer.Context) -> None:
    """Run the MCP server on stdio (the default MCP transport)."""
    cfg = ctx.obj or {}
    try:
        from ...mcp import run_stdio
    except ImportError as exc:
        typer.echo(
            'MCP support requires the optional extra: pip install "dvr[mcp]"',
            err=True,
        )
        raise typer.Exit(1) from exc

    run_stdio(
        auto_launch=cfg.get("auto_launch", True),
        timeout=cfg.get("timeout", 30.0),
    )


# ---------------------------------------------------------------------------
# tools (introspect without spawning the server)
# ---------------------------------------------------------------------------


@app.command("tools")
def tools(
    ctx: typer.Context,
    detail: Annotated[
        bool,
        typer.Option(
            "--detail",
            "-d",
            help="Print full descriptions and JSON schemas (default: name + summary).",
        ),
    ] = False,
) -> None:
    """List the MCP tools dvr exposes to LLM agents."""
    try:
        from ...mcp import list_tools_metadata
    except ImportError as exc:
        typer.echo(
            'MCP support requires the optional extra: pip install "dvr[mcp]"',
            err=True,
        )
        raise typer.Exit(1) from exc

    payload = list_tools_metadata()

    fmt = (ctx.obj or {}).get("format")
    if detail or fmt == "json":
        output.emit(payload, fmt=fmt or "json")
        return

    summary = [
        {
            "name": t["name"],
            "needs_resolve": t["needs_resolve"],
            "summary": t["description"].split(".")[0].strip() + ".",
        }
        for t in payload
    ]
    output.emit(summary, fmt=fmt, headline=f"{len(payload)} dvr MCP tools")


# ---------------------------------------------------------------------------
# Client config helpers (Claude Desktop, Cursor, Continue, etc. all use the
# same JSON shape: { "mcpServers": { "<name>": { "command", "args", "env" } } })
# ---------------------------------------------------------------------------


def _claude_desktop_config_path() -> Path:
    """Return the platform-appropriate Claude Desktop config path."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def _cursor_config_path() -> Path:
    """Cursor's MCP config (same shape as Claude Desktop)."""
    return Path.home() / ".cursor/mcp.json"


def _resolve_command() -> list[str]:
    """Return [command, ...args] for spawning the dvr MCP server."""
    dvr_path = shutil.which("dvr")
    if dvr_path:
        return [dvr_path, "mcp", "serve"]
    # Fallback: invoke through the active Python interpreter.
    return [sys.executable, "-m", "dvr", "mcp", "serve"]


def _build_server_entry(
    *,
    enable_eval: bool,
    no_launch: bool,
    timeout: float,
) -> tuple[dict[str, Any], list[str]]:
    """Return (server_entry, full_command_words) for an MCP client config."""
    cmd = _resolve_command()
    args: list[str] = []
    if no_launch:
        args.append("--no-launch")
    if timeout != 30.0:
        args.extend(["--timeout", str(timeout)])
    args.extend(cmd[1:])
    server_entry: dict[str, Any] = {"command": cmd[0], "args": args}
    if enable_eval:
        server_entry["env"] = {"DVR_MCP_ENABLE_EVAL": "1"}
    return server_entry, [cmd[0], *args]


def _write_client_config(
    path: Path,
    *,
    name: str,
    server_entry: dict[str, Any],
    force: bool,
    dry_run: bool,
    client_label: str,
) -> None:
    """Merge a single ``mcpServers.<name>`` entry into ``path``, preserving
    everything else. Raises ``typer.Exit`` on conflict without --force."""
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as exc:
            typer.echo(f"refusing to overwrite invalid JSON at {path}: {exc}", err=True)
            raise typer.Exit(1) from exc

    servers = dict(existing.get("mcpServers") or {})
    prior = servers.get(name)
    if prior and not force and prior != server_entry:
        typer.echo(
            f"server '{name}' already exists in {path} with different settings.\n"
            f"  current: {json.dumps(prior)}\n"
            f"  new:     {json.dumps(server_entry)}\n"
            f"re-run with --force to overwrite.",
            err=True,
        )
        raise typer.Exit(1)
    servers[name] = server_entry
    new_config = {**existing, "mcpServers": servers}
    rendered = json.dumps(new_config, indent=2)

    if dry_run:
        typer.echo(f"# would write {path}\n{rendered}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered + "\n")
    cmd_words = [server_entry["command"], *server_entry.get("args", [])]
    typer.echo(
        f"wrote {path}\n"
        f"   server '{name}' -> {' '.join(cmd_words)}\n"
        f"   restart {client_label} to pick up the change."
    )


def _install_options() -> None:
    """Marker for shared install options (decorators below)."""


_NameOpt = Annotated[
    str,
    typer.Option("--name", "-n", help="MCP server name as it will appear to the client."),
]
_EnableEvalOpt = Annotated[
    bool,
    typer.Option(
        "--enable-eval",
        help="Enable the `eval` tool by setting DVR_MCP_ENABLE_EVAL=1 in the server's env.",
    ),
]
_NoLaunchOpt = Annotated[
    bool,
    typer.Option(
        "--no-launch/--launch",
        help=(
            "Whether the registered server should auto-launch Resolve. Default: "
            "--no-launch so a single failed connect can't blow Claude's tool-call timeout."
        ),
    ),
]
_TimeoutOpt = Annotated[
    float,
    typer.Option(
        "--timeout",
        help="Connection timeout (seconds) the registered server uses. Default 5s.",
    ),
]
_DryRunOpt = Annotated[
    bool, typer.Option("--dry-run", help="Print the resulting config but don't write it.")
]
_ForceOpt = Annotated[
    bool, typer.Option("--force", "-f", help="Overwrite an existing entry of the same name.")
]


@app.command("install")
def install(
    config_path: Annotated[
        Path,
        typer.Argument(
            help=(
                "Path to a JSON file using the standard `mcpServers` shape "
                "(Claude Desktop, Cursor, Continue, etc.)."
            ),
        ),
    ],
    name: _NameOpt = "dvr",
    enable_eval: _EnableEvalOpt = False,
    no_launch: _NoLaunchOpt = True,
    timeout: _TimeoutOpt = 5.0,
    dry_run: _DryRunOpt = False,
    force: _ForceOpt = False,
) -> None:
    """Generic installer: write the dvr MCP server entry into any client config
    that uses the standard ``mcpServers`` JSON shape.

    Use this for clients that aren't covered by ``install-claude`` /
    ``install-cursor`` / ``install-claude-code`` shortcuts.
    """
    server_entry, _ = _build_server_entry(
        enable_eval=enable_eval, no_launch=no_launch, timeout=timeout
    )
    _write_client_config(
        config_path,
        name=name,
        server_entry=server_entry,
        force=force,
        dry_run=dry_run,
        client_label="the MCP client",
    )


@app.command("install-claude")
def install_claude(
    name: _NameOpt = "dvr",
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to claude_desktop_config.json (defaults to the platform path).",
        ),
    ] = None,
    enable_eval: _EnableEvalOpt = False,
    no_launch: _NoLaunchOpt = True,
    timeout: _TimeoutOpt = 5.0,
    dry_run: _DryRunOpt = False,
    force: _ForceOpt = False,
) -> None:
    """Add (or update) the dvr MCP server in Claude Desktop's config.

    On macOS this writes to::

        ~/Library/Application Support/Claude/claude_desktop_config.json
    """
    path = config_path or _claude_desktop_config_path()
    server_entry, _ = _build_server_entry(
        enable_eval=enable_eval, no_launch=no_launch, timeout=timeout
    )
    _write_client_config(
        path,
        name=name,
        server_entry=server_entry,
        force=force,
        dry_run=dry_run,
        client_label="Claude Desktop",
    )


@app.command("install-cursor")
def install_cursor(
    name: _NameOpt = "dvr",
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to Cursor's mcp.json (defaults to ~/.cursor/mcp.json).",
        ),
    ] = None,
    enable_eval: _EnableEvalOpt = False,
    no_launch: _NoLaunchOpt = True,
    timeout: _TimeoutOpt = 5.0,
    dry_run: _DryRunOpt = False,
    force: _ForceOpt = False,
) -> None:
    """Add (or update) the dvr MCP server in Cursor's config (~/.cursor/mcp.json)."""
    path = config_path or _cursor_config_path()
    server_entry, _ = _build_server_entry(
        enable_eval=enable_eval, no_launch=no_launch, timeout=timeout
    )
    _write_client_config(
        path,
        name=name,
        server_entry=server_entry,
        force=force,
        dry_run=dry_run,
        client_label="Cursor",
    )


# ---------------------------------------------------------------------------
# install-claude-code (Claude Code CLI helper)
# ---------------------------------------------------------------------------


@app.command("install-claude-code")
def install_claude_code(
    name: Annotated[
        str,
        typer.Option("--name", "-n", help="MCP server name as it will appear to Claude Code."),
    ] = "dvr",
    scope: Annotated[
        str,
        typer.Option(
            "--scope",
            "-s",
            help="`local`, `user`, or `project`. Defaults to `user` (visible everywhere).",
        ),
    ] = "user",
    enable_eval: Annotated[
        bool,
        typer.Option(
            "--enable-eval",
            help="Enable the `eval` tool (sets DVR_MCP_ENABLE_EVAL=1).",
        ),
    ] = False,
    no_launch: Annotated[
        bool,
        typer.Option(
            "--no-launch/--launch",
            help="Whether the registered server should auto-launch Resolve.",
        ),
    ] = True,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Connection timeout (seconds) the registered server uses.",
        ),
    ] = 5.0,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Replace an existing entry of the same name."),
    ] = False,
) -> None:
    """Add (or update) the dvr MCP server in Claude Code's config (via `claude` CLI).

    Equivalent to running::

        claude mcp add -s user dvr -- dvr --no-launch --timeout 5 mcp serve

    plus optional ``-e DVR_MCP_ENABLE_EVAL=1`` and ``--force`` handling.
    Requires the Claude Code CLI (``claude``) to be installed.
    """
    import subprocess

    claude_path = shutil.which("claude")
    if not claude_path:
        typer.echo(
            "claude CLI not found. Install it from https://docs.anthropic.com/claude/claude-code "
            "or use `dvr mcp install-claude` for Claude Desktop instead.",
            err=True,
        )
        raise typer.Exit(1)

    cmd = _resolve_command()
    server_args = []
    if no_launch:
        server_args.append("--no-launch")
    if timeout != 30.0:
        server_args.extend(["--timeout", str(timeout)])
    server_args.extend(cmd[1:])  # 'mcp serve'

    # Check existing
    list_result = subprocess.run([claude_path, "mcp", "list"], capture_output=True, text=True)
    if name in list_result.stdout:
        if not force:
            typer.echo(
                f"server '{name}' already exists. Re-run with --force to replace.",
                err=True,
            )
            raise typer.Exit(1)
        subprocess.run(
            [claude_path, "mcp", "remove", name, "-s", scope],
            capture_output=True,
            text=True,
        )

    add_cmd = [claude_path, "mcp", "add", "-s", scope]
    if enable_eval:
        add_cmd.extend(["-e", "DVR_MCP_ENABLE_EVAL=1"])
    add_cmd.append(name)
    add_cmd.append("--")
    add_cmd.append(cmd[0])
    add_cmd.extend(server_args)
    result = subprocess.run(add_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        typer.echo(f"failed to register: {result.stderr or result.stdout}", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"registered '{name}' with Claude Code (scope: {scope})\n"
        f"   command: {cmd[0]} {' '.join(server_args)}\n"
        f"   verify with: claude mcp list"
    )
