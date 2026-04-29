"""Tests for the MCP server.

These exercise the tool registry, dispatcher, and full stdio JSON-RPC
handshake without requiring DaVinci Resolve.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

pytest.importorskip("mcp")
pytest.importorskip("dvr.mcp.server")


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


def test_registry_lists_expected_tools() -> None:
    from dvr.mcp.server import _build_registry

    names = {s.name for s in _build_registry()}
    must = {
        "version",
        "doctor",
        "reconnect",
        "schema",
        "snapshot_list",
        "ping",
        "inspect",
        "page_get",
        "page_set",
        "project_list",
        "project_ensure",
        "timeline_list",
        "timeline_inspect",
        "marker_add",
        "clip_where",
        "render_queue",
        "render_submit",
        "media_inspect",
        "media_scan",
        "media_bin_ensure",
        "media_move",
        "timeline_append",
        "interchange_export",
        "diff_timelines",
        "apply_spec",
        "snapshot_save",
        "lint",
        "eval",
    }
    assert must.issubset(names), f"missing: {must - names}"


def test_no_resolve_tools_marked_correctly() -> None:
    from dvr.mcp.server import _build_registry

    no_resolve = {s.name for s in _build_registry() if not s.needs_resolve}
    assert {"version", "doctor", "reconnect", "schema", "snapshot_list"}.issubset(no_resolve)


def test_all_schemas_are_valid_json_schema_shape() -> None:
    from dvr.mcp.server import _build_registry

    for spec in _build_registry():
        assert spec.schema.get("type") == "object", spec.name
        props = spec.schema.get("properties")
        assert isinstance(props, dict), spec.name


def test_list_tools_metadata_round_trips_to_json() -> None:
    from dvr.mcp import list_tools_metadata

    payload = list_tools_metadata()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload
    assert all("name" in t for t in payload)
    assert all("description" in t for t in payload)
    assert all("input_schema" in t for t in payload)


# ---------------------------------------------------------------------------
# Dispatcher with no live connection
# ---------------------------------------------------------------------------


def _dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    from dvr.mcp.server import _build_registry, _ResolveCache
    from dvr.mcp.server import _dispatch as _d

    cache = _ResolveCache(auto_launch=False, timeout=2.0)
    registry = {s.name: s for s in _build_registry()}
    result = _d(registry, cache, name, args)
    assert len(result.content) == 1
    return json.loads(result.content[0].text)


def test_dispatch_version_returns_metadata() -> None:
    payload = _dispatch("version", {})
    assert "dvr" in payload
    assert payload["python"] == sys.version.split()[0]
    assert payload["brand"]["name"] == "dvr"
    assert payload["brand"]["logo"].endswith("logo.png")
    assert payload["brand"]["icon"].endswith("icon.png")


def test_dispatch_unknown_tool_returns_structured_error() -> None:
    from dvr.mcp.server import _build_registry, _ResolveCache
    from dvr.mcp.server import _dispatch as _d

    cache = _ResolveCache(auto_launch=False, timeout=2.0)
    registry = {s.name: s for s in _build_registry()}
    result = _d(registry, cache, "no_such_tool", {})
    payload = json.loads(result.content[0].text)
    assert result.isError is True
    assert "error" in payload
    assert payload["error"]["type"] == "DvrError"
    assert "Unknown tool" in payload["error"]["message"]


def test_dispatch_eval_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DVR_MCP_ENABLE_EVAL", raising=False)
    payload = _dispatch("eval", {"expression": "1 + 1"})
    assert "error" in payload
    assert "disabled" in payload["error"]["message"].lower()


def test_dispatch_doctor_does_not_connect_by_default() -> None:
    payload = _dispatch("doctor", {})
    assert payload["brand"]["name"] == "dvr"
    assert "scripting_lib_present" in payload
    assert "connection_cached" in payload
    # Without probe=true we never try to connect; "connected" must be absent.
    assert "connected" not in payload


def test_dispatch_schema_static_topic_does_not_connect() -> None:
    payload = _dispatch("schema", {"topic": "clip-properties"})
    assert "Pan" in payload
    assert payload["Pan"]["type"] == "float"


def test_dispatch_schema_unknown_topic_returns_error() -> None:
    payload = _dispatch("schema", {"topic": "nonexistent"})
    assert "error" in payload


def test_dispatch_snapshot_list_is_empty_or_listy(tmp_path: Any) -> None:
    payload = _dispatch("snapshot_list", {})
    assert isinstance(payload, list)


def test_dispatch_media_scan_skips_appledouble_files(tmp_path: Any) -> None:
    video = tmp_path / "shot.mov"
    sidecar = tmp_path / "._shot.mov"
    audio = tmp_path / "stem.wav"
    text = tmp_path / "notes.txt"
    video.write_bytes(b"video")
    sidecar.write_bytes(b"sidecar")
    audio.write_bytes(b"audio")
    text.write_text("ignore")

    payload = _dispatch("media_scan", {"path": str(tmp_path)})

    assert payload["file_count"] == 2
    assert payload["counts"] == {"audio": 1, "video": 1}
    assert {item["name"] for item in payload["files"]} == {"shot.mov", "stem.wav"}


def test_resolve_cache_caches_failures() -> None:
    """A failed connect must be cached so we don't keep stabbing fusionscript."""
    from dvr import errors
    from dvr.mcp.server import _ResolveCache

    cache = _ResolveCache(auto_launch=False, timeout=2.0, failure_ttl=30.0)

    calls = {"n": 0}

    class _StubResolve:
        def __init__(self, **_: Any) -> None:
            calls["n"] += 1
            raise errors.ConnectionError("simulated failure")

    import dvr.mcp.server as srv_mod

    real_resolve = srv_mod.Resolve
    srv_mod.Resolve = _StubResolve  # type: ignore[misc]
    try:
        for _ in range(5):
            with pytest.raises(errors.ConnectionError):
                cache.get()
        assert calls["n"] == 1, f"expected 1 underlying connect, got {calls['n']}"
    finally:
        srv_mod.Resolve = real_resolve  # type: ignore[misc]


def test_resolve_cache_reset_clears_failure() -> None:
    """`reconnect` must clear the cached failure so a retry happens."""
    from dvr import errors
    from dvr.mcp.server import _ResolveCache

    cache = _ResolveCache(auto_launch=False, timeout=2.0, failure_ttl=30.0)

    calls = {"n": 0}

    class _StubResolve:
        def __init__(self, **_: Any) -> None:
            calls["n"] += 1
            raise errors.ConnectionError("sim")

    import dvr.mcp.server as srv_mod

    real_resolve = srv_mod.Resolve
    srv_mod.Resolve = _StubResolve  # type: ignore[misc]
    try:
        with pytest.raises(errors.ConnectionError):
            cache.get()
        with pytest.raises(errors.ConnectionError):
            cache.get()
        assert calls["n"] == 1

        cache.reset()
        with pytest.raises(errors.ConnectionError):
            cache.get()
        assert calls["n"] == 2
    finally:
        srv_mod.Resolve = real_resolve  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Full stdio JSON-RPC handshake
# ---------------------------------------------------------------------------


def _send_jsonrpc(method: str, params: dict[str, Any] | None = None, *, msg_id: int = 1) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode("utf-8")


def _send_notification(method: str, params: dict[str, Any] | None = None) -> bytes:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    return (json.dumps(body) + "\n").encode("utf-8")


def _readline_with_timeout(stream: Any, timeout: float) -> bytes:
    """Read one line from ``stream`` or raise TimeoutError after ``timeout`` seconds."""
    import threading

    box: list[bytes | BaseException] = []

    def reader() -> None:
        try:
            box.append(stream.readline())
        except BaseException as exc:  # pragma: no cover
            box.append(exc)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise TimeoutError(f"no line within {timeout}s")
    if not box:  # pragma: no cover
        raise RuntimeError("reader produced no result")
    out = box[0]
    if isinstance(out, BaseException):  # pragma: no cover
        raise out
    return out


def test_mcp_stdio_initialize_and_list_tools() -> None:
    """Spawn `dvr mcp serve` as a subprocess and speak MCP over stdio."""
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-m", "dvr", "--no-launch", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        proc.stdin.write(
            _send_jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "dvr-test", "version": "0"},
                },
                msg_id=1,
            )
        )
        proc.stdin.flush()
        init_response = json.loads(_readline_with_timeout(proc.stdout, 10.0))
        assert init_response["id"] == 1
        assert "result" in init_response
        assert init_response["result"]["serverInfo"]["name"] == "dvr"

        proc.stdin.write(_send_notification("notifications/initialized"))
        proc.stdin.flush()

        proc.stdin.write(_send_jsonrpc("tools/list", {}, msg_id=2))
        proc.stdin.flush()
        tools_response = json.loads(_readline_with_timeout(proc.stdout, 5.0))
        assert tools_response["id"] == 2
        tool_names = {t["name"] for t in tools_response["result"]["tools"]}
        assert "version" in tool_names
        assert "doctor" in tool_names
        assert "ping" in tool_names

        proc.stdin.write(
            _send_jsonrpc(
                "tools/call",
                {"name": "version", "arguments": {}},
                msg_id=3,
            )
        )
        proc.stdin.flush()
        call_response = json.loads(_readline_with_timeout(proc.stdout, 5.0))
        assert call_response["id"] == 3
        content = call_response["result"]["content"]
        assert len(content) == 1
        payload = json.loads(content[0]["text"])
        assert "dvr" in payload
        assert payload["python"] == sys.version.split()[0]

        proc.stdin.write(
            _send_jsonrpc(
                "tools/call",
                {"name": "doctor", "arguments": {}},
                msg_id=4,
            )
        )
        proc.stdin.flush()
        doctor_response = json.loads(_readline_with_timeout(proc.stdout, 5.0))
        doc_payload = json.loads(doctor_response["result"]["content"][0]["text"])
        assert "scripting_lib_present" in doc_payload
        assert "platform" in doc_payload
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        if proc.returncode not in (0, None):
            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise AssertionError(f"server exited with {proc.returncode}: {stderr}")
