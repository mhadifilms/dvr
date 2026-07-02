"""Tests for daemon-executed CLI commands and transparent forwarding."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dvr import daemon
from dvr.cli import session
from dvr.cli.main import _should_bypass_daemon


def test_run_cli_executes_commands_in_process() -> None:
    result = daemon.run_cli(["--format", "json", "doctor"])
    assert result["exit_code"] == 0
    payload = json.loads(result["stdout"])
    assert "scripting_lib_present" in payload


def test_run_cli_reports_usage_errors() -> None:
    result = daemon.run_cli(["definitely-not-a-command"])
    assert result["exit_code"] != 0
    assert "definitely-not-a-command" in result["stderr"] or "Usage" in result["stderr"]


def test_methods_includes_cli() -> None:
    assert "cli" in daemon.methods()


def test_session_provider_overrides_construction() -> None:
    sentinel = object()
    session.set_resolve_provider(lambda: sentinel)  # type: ignore[arg-type, return-value]
    try:
        ctx = SimpleNamespace(obj={})
        assert session.resolve_from_ctx(ctx) is sentinel  # type: ignore[arg-type]
    finally:
        session.set_resolve_provider(None)


@pytest.mark.parametrize(
    ("argv", "bypass"),
    [
        (["serve", "start"], True),
        (["mcp", "serve"], True),
        (["repl"], True),
        (["doctor"], True),
        (["--help"], True),
        (["render", "watch"], True),
        (["render", "submit", "--wait", "--target-dir", "/x"], True),
        (["project", "list"], False),
        (["timeline", "inspect"], False),
        (["--format", "json", "media", "bins"], False),
    ],
)
def test_should_bypass_daemon(
    argv: list[str], bypass: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DVR_NO_DAEMON", raising=False)
    if sys.platform == "win32":
        assert _should_bypass_daemon(argv) is True
        return
    assert _should_bypass_daemon(argv) is bypass


def test_dvr_no_daemon_env_bypasses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DVR_NO_DAEMON", "1")
    assert _should_bypass_daemon(["project", "list"]) is True


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-domain sockets only")
def test_daemon_serves_cli_over_socket() -> None:
    # Keep the socket path short — macOS caps AF_UNIX paths at ~104 chars.
    with tempfile.TemporaryDirectory(prefix="dvrt", dir="/tmp") as tmp:
        sock = Path(tmp) / "dvr.sock"
        server = daemon._Server(str(sock), auto_launch=False, connect_timeout=1.0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = daemon.Client(path=sock, timeout=15.0)
            result = client.call("cli", {"argv": ["--format", "json", "doctor"]})
            assert result["exit_code"] == 0
            payload = json.loads(result["stdout"])
            assert payload["platform"] == sys.platform
        finally:
            server.shutdown()
            server.server_close()
