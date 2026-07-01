"""CLI-level tests that don't need a live Resolve: doctor, media scan, serve, errors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dvr import errors
from dvr.cli import output
from dvr.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_session_format() -> Any:
    yield
    output.set_session_format(None)


def test_doctor_runs_without_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DVR_FORMAT", "json")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "scripting_lib_present" in payload
    assert "connected" not in payload  # static mode: no connection attempt


def test_media_scan_lists_media_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DVR_FORMAT", "json")
    (tmp_path / "clip.mov").write_bytes(b"x")
    (tmp_path / "._clip.mov").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")

    result = runner.invoke(app, ["media", "scan", str(tmp_path)])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["name"] for r in rows] == ["clip.mov"]


def test_serve_start_background_places_no_launch_before_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dvr.cli.commands import serve as serve_cmd

    monkeypatch.setenv("DVR_FORMAT", "json")
    monkeypatch.setattr(serve_cmd.daemon, "status", lambda: {"running": False})

    captured: dict[str, Any] = {}

    class _Proc:
        pid = 4242

    def _fake_popen(cmd: list[str], **kwargs: Any) -> _Proc:
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(serve_cmd.subprocess, "Popen", _fake_popen)

    result = runner.invoke(app, ["--no-launch", "serve", "start"])
    assert result.exit_code == 0, result.output
    # --no-launch is a *root* option: it must come after "dvr" and before "serve".
    assert captured["cmd"] == [
        sys.executable,
        "-m",
        "dvr",
        "--no-launch",
        "serve",
        "start",
        "--foreground",
    ]


def test_serve_methods_uses_public_daemon_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DVR_FORMAT", "json")
    result = runner.invoke(app, ["serve", "methods"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    names = [r["method"] for r in rows]
    assert names == sorted(names)
    assert "timeline.inspect" in names


def test_main_renders_dvr_errors_as_structured_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dvr.cli import main as main_mod

    monkeypatch.setenv("DVR_FORMAT", "json")

    def _boom() -> None:
        raise errors.ConnectionError("cannot reach Resolve", fix="launch Resolve")

    monkeypatch.setattr(main_mod, "app", _boom)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["type"] == "ConnectionError"
    assert payload["fix"] == "launch Resolve"
