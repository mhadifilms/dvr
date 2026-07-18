"""Record/replay harness for Resolve scripting traffic.

Hand-written mocks encode what we *believe* Resolve does. This module
captures what it *actually* does: run any dvr code once against a real
Resolve with ``DVR_RECORD=/path/to/cassette.jsonl`` set, and every
scripting call (method, arguments, result) is appended to the cassette.
Replay the cassette later — in CI, on a machine without Resolve — and
the same code runs against the recorded responses, raising loudly if it
diverges from what was recorded.

Record (needs Resolve)::

    DVR_RECORD=session.jsonl dvr timeline inspect

or in Python::

    from dvr import vcr
    handle = vcr.wrap_recording(raw_handle, "session.jsonl")

Replay (no Resolve needed)::

    from dvr import vcr
    r = vcr.resolve_from_cassette("session.jsonl")   # a dvr.Resolve
    r.timeline.current.inspect()                      # served from disk

Cassette format: one JSON object per line —
``{"handle": "h0", "method": "GetName", "args": [...], "kwargs": {...},
"result": ...}``. Opaque scripting objects are encoded as
``{"__handle__": "hN"}``; handle numbering is assigned in creation order,
so replays match as long as the calling code is deterministic.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import errors

_HANDLE_KEY = "__handle__"


def _is_opaque(value: Any) -> bool:
    return not isinstance(value, (type(None), bool, int, float, str, list, tuple, dict))


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class _Recording:
    """Shared state for one cassette being written."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._handles: dict[int, str] = {}  # id(obj) -> "hN"
        self._keep_alive: list[Any] = []  # prevent id() reuse via GC
        self._counter = 0

    def handle_id(self, obj: Any) -> str:
        key = id(obj)
        if key not in self._handles:
            self._handles[key] = f"h{self._counter}"
            self._counter += 1
            self._keep_alive.append(obj)
        return self._handles[key]

    def encode(self, value: Any) -> Any:
        if isinstance(value, RecordingProxy):
            return {_HANDLE_KEY: value._vcr_handle_id}
        if isinstance(value, (list, tuple)):
            return [self.encode(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self.encode(v) for k, v in value.items()}
        if _is_opaque(value):
            return {_HANDLE_KEY: self.handle_id(value)}
        return value

    def write(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._file.write(json.dumps(entry, default=str) + "\n")
            self._file.flush()


class RecordingProxy:
    """Wraps an opaque scripting handle; records every method call through it."""

    __slots__ = ("_vcr_handle_id", "_vcr_recording", "_vcr_target")

    def __init__(self, target: Any, recording: _Recording) -> None:
        object.__setattr__(self, "_vcr_target", target)
        object.__setattr__(self, "_vcr_recording", recording)
        object.__setattr__(self, "_vcr_handle_id", recording.handle_id(target))

    def __getattr__(self, method: str) -> Any:
        target = object.__getattribute__(self, "_vcr_target")
        recording: _Recording = object.__getattribute__(self, "_vcr_recording")
        attr = getattr(target, method)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            real_args = _unwrap_recording(args)
            real_kwargs = _unwrap_recording(kwargs)
            result = attr(*real_args, **real_kwargs)
            recording.write(
                {
                    "handle": object.__getattribute__(self, "_vcr_handle_id"),
                    "method": method,
                    "args": recording.encode(list(args)),
                    "kwargs": recording.encode(dict(kwargs)),
                    "result": recording.encode(result),
                }
            )
            return _proxy_result(result, recording)

        return call


def _unwrap_recording(value: Any) -> Any:
    """Replace proxies at any argument depth before calling Resolve."""
    if isinstance(value, RecordingProxy):
        return object.__getattribute__(value, "_vcr_target")
    if isinstance(value, tuple):
        return tuple(_unwrap_recording(item) for item in value)
    if isinstance(value, list):
        return [_unwrap_recording(item) for item in value]
    if isinstance(value, dict):
        return {key: _unwrap_recording(item) for key, item in value.items()}
    return value


def _proxy_result(value: Any, recording: _Recording) -> Any:
    if _is_opaque(value):
        return RecordingProxy(value, recording)
    if isinstance(value, list):
        return [_proxy_result(v, recording) for v in value]
    if isinstance(value, dict):
        return {k: _proxy_result(v, recording) for k, v in value.items()}
    return value


def wrap_recording(handle: Any, path: str | Path) -> RecordingProxy:
    """Wrap a raw scripting handle so every call is recorded to ``path``."""
    return RecordingProxy(handle, _Recording(path))


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class _Cassette:
    """Parsed cassette with FIFO queues keyed by (handle, method, args)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        if not self.path.exists():
            raise errors.DvrError(
                f"Cassette not found: {self.path}",
                fix="Record one first: DVR_RECORD=<path> dvr <command> (with Resolve running).",
            )
        self._queues: dict[str, list[dict[str, Any]]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            self._queues.setdefault(self._key(entry), []).append(entry)

    @staticmethod
    def _key(entry: dict[str, Any]) -> str:
        return json.dumps(
            [entry["handle"], entry["method"], entry.get("args"), entry.get("kwargs")],
            sort_keys=True,
        )

    def pop(self, handle: str, method: str, args: Any, kwargs: Any) -> dict[str, Any]:
        key = self._key({"handle": handle, "method": method, "args": args, "kwargs": kwargs})
        queue = self._queues.get(key)
        if not queue:
            raise errors.DvrError(
                f"Replay diverged: no recorded call {method}({args}) on {handle}.",
                cause="The code path differs from the recorded session (or the cassette is exhausted).",
                fix="Re-record the cassette against a live Resolve.",
                state={
                    "handle": handle,
                    "method": method,
                    "args": args,
                    "cassette": str(self.path),
                },
            )
        return queue.pop(0)


class ReplayHandle:
    """A fake scripting handle that serves method calls from a cassette."""

    __slots__ = ("_vcr_cassette", "_vcr_handle_id")

    def __init__(self, cassette: _Cassette, handle_id: str = "h0") -> None:
        object.__setattr__(self, "_vcr_cassette", cassette)
        object.__setattr__(self, "_vcr_handle_id", handle_id)

    def __getattr__(self, method: str) -> Any:
        cassette: _Cassette = object.__getattribute__(self, "_vcr_cassette")
        handle_id: str = object.__getattribute__(self, "_vcr_handle_id")

        def call(*args: Any, **kwargs: Any) -> Any:
            encoded_args = _encode_replay(list(args))
            encoded_kwargs = _encode_replay(dict(kwargs))
            entry = cassette.pop(handle_id, method, encoded_args, encoded_kwargs)
            return _decode_replay(entry.get("result"), cassette)

        return call


def _encode_replay(value: Any) -> Any:
    if isinstance(value, ReplayHandle):
        return {_HANDLE_KEY: object.__getattribute__(value, "_vcr_handle_id")}
    if isinstance(value, (list, tuple)):
        return [_encode_replay(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _encode_replay(v) for k, v in value.items()}
    return value


def _decode_replay(value: Any, cassette: _Cassette) -> Any:
    if isinstance(value, dict):
        if set(value.keys()) == {_HANDLE_KEY}:
            return ReplayHandle(cassette, value[_HANDLE_KEY])
        return {k: _decode_replay(v, cassette) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_replay(v, cassette) for v in value]
    return value


def replay_raw(path: str | Path) -> ReplayHandle:
    """Return a fake raw Resolve handle that replays ``path``."""
    return ReplayHandle(_Cassette(path))


def resolve_from_cassette(path: str | Path) -> Any:
    """Construct a :class:`dvr.Resolve` served entirely from a cassette.

    The cassette must have been recorded from connection onward (the
    ``DVR_RECORD`` env var does this), because ``Resolve()`` reads the
    ProjectManager during construction.
    """
    from .resolve import Resolve

    raw = replay_raw(path)
    r = Resolve.__new__(Resolve)
    r._raw = raw
    r._project_manager = raw.GetProjectManager()
    return r


__all__ = [
    "RecordingProxy",
    "ReplayHandle",
    "replay_raw",
    "resolve_from_cassette",
    "wrap_recording",
]
