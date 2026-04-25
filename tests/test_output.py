"""Tests for CLI output formatting (no Resolve required)."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

from dvr.cli import output


def _capture_emit(value: object, fmt: str) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        output.emit(value, fmt=fmt)
    return buffer.getvalue()


def test_emit_json_dict() -> None:
    text = _capture_emit({"a": 1, "b": "two"}, fmt="json")
    parsed = json.loads(text)
    assert parsed == {"a": 1, "b": "two"}


def test_emit_json_list_of_dicts() -> None:
    rows = [{"name": "x", "n": 1}, {"name": "y", "n": 2}]
    parsed = json.loads(_capture_emit(rows, fmt="json"))
    assert parsed == rows


def test_emit_yaml_dict() -> None:
    import yaml

    text = _capture_emit({"a": 1, "b": "two"}, fmt="yaml")
    parsed = yaml.safe_load(text)
    assert parsed == {"a": 1, "b": "two"}


def test_resolve_format_explicit_wins(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DVR_FORMAT", "yaml")
    assert output.resolve_format("json") == "json"


def test_resolve_format_env_used(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DVR_FORMAT", "json")
    assert output.resolve_format(None) == "json"


def test_resolve_format_tty_falls_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DVR_FORMAT", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert output.resolve_format(None) == "json"
