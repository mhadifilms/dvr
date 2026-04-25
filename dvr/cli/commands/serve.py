"""``dvr serve`` sub-commands — daemon mode."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Annotated

import typer

from ... import daemon
from .. import output

app = typer.Typer(name="serve", help="Run a local daemon that holds a Resolve connection.")


@app.command("start")
def start(
    ctx: typer.Context,
    background: Annotated[
        bool,
        typer.Option(
            "--background/--foreground",
            help="Run in the background (default) or stay in the foreground.",
        ),
    ] = True,
) -> None:
    """Start the daemon."""
    cfg = ctx.obj or {}
    auto_launch = cfg.get("auto_launch", True)
    timeout = cfg.get("timeout", 30.0)

    state = daemon.status()
    if state["running"]:
        output.emit(state, fmt=cfg.get("format"), headline="already running")
        return

    if not background:
        daemon.serve(auto_launch=auto_launch, timeout=timeout)
        return

    # Detach in the background.
    cmd = [
        sys.executable,
        "-m",
        "dvr",
        "serve",
        "start",
        "--foreground",
    ]
    if not auto_launch:
        cmd.insert(2, "--no-launch")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "DVR_FORMAT": cfg.get("format") or "json"},
    )
    output.emit(
        {"started": True, "pid": proc.pid, "socket": str(daemon.socket_path())},
        fmt=cfg.get("format"),
    )


@app.command("stop")
def stop(ctx: typer.Context) -> None:
    """Stop the running daemon."""
    cfg = ctx.obj or {}
    stopped = daemon.stop_daemon()
    output.emit({"stopped": bool(stopped)}, fmt=cfg.get("format"))


@app.command("status")
def status_cmd(ctx: typer.Context) -> None:
    """Show daemon status."""
    cfg = ctx.obj or {}
    output.emit(daemon.status(), fmt=cfg.get("format"), headline="dvr serve")


@app.command("methods")
def methods(ctx: typer.Context) -> None:
    """List allow-listed RPC methods."""
    cfg = ctx.obj or {}
    rows = [{"method": name} for name in sorted(daemon._METHODS.keys())]
    output.emit(rows, fmt=cfg.get("format"), headline="rpc methods")
