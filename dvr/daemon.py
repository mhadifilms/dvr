"""Local daemon that holds a Resolve connection across CLI invocations.

Resolve's cold connection on macOS takes 2-3 seconds. For LLM agents and
shell scripts running many sequential commands, that handshake dominates
the wall-clock cost. The daemon avoids it by running once in the
background and serving requests over a Unix-domain socket.

Wire format
-----------

Newline-delimited JSON. One request per line, one response per line:

    {"id": "<correlation-id>", "method": "timeline.inspect", "params": {}}
    -> {"id": "<correlation-id>", "ok": true,  "result": {...}}
    -> {"id": "<correlation-id>", "ok": false, "error": {...}}

Methods are dotted paths into the public library (e.g. ``timeline.inspect``,
``project.list``, ``render.queue``, ``app.page``). Arguments are passed as
``params`` (a list for positional, dict for keyword, or a single dict for
mixed). The daemon validates dispatch against an explicit allow-list to
prevent arbitrary attribute traversal.

Socket location
---------------

* Linux/macOS: ``$XDG_RUNTIME_DIR/dvr/dvr.sock`` if set, otherwise
  ``~/.cache/dvr/dvr.sock``.
* Windows: not supported by this module (use the in-process library).

The socket is mode 0600 — same-user only.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import sys
import threading
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from . import errors
from .resolve import Resolve

logger = logging.getLogger("dvr.daemon")


# ---------------------------------------------------------------------------
# Socket location
# ---------------------------------------------------------------------------


def socket_path() -> Path:
    """Return the conventional socket path for this user."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime_dir) if runtime_dir else Path.home() / ".cache"
    target = base / "dvr"
    target.mkdir(parents=True, exist_ok=True)
    return target / "dvr.sock"


def pid_path() -> Path:
    return socket_path().with_suffix(".pid")


# ---------------------------------------------------------------------------
# Method dispatch
# ---------------------------------------------------------------------------


# Allow-list of methods. Each value is the dotted attribute path on a
# ``Resolve`` instance, plus a flag indicating whether it's a method
# (call with params) or a property (read).
_METHODS: dict[str, tuple[str, bool]] = {
    "app.inspect": ("app.inspect", True),
    "app.page.get": ("app.page", False),
    "app.page.set": ("app.set_page", True),  # synthetic — see _dispatch
    "app.version": ("app.version", False),
    "app.product": ("app.product", False),
    "app.quit": ("app.quit", True),
    "inspect": ("inspect", True),
    "project.list": ("project.list", True),
    "project.current": ("project.current", False),
    "project.create": ("project.create", True),
    "project.load": ("project.load", True),
    "project.ensure": ("project.ensure", True),
    "project.delete": ("project.delete", True),
    "project.save": ("project.save", True),  # synthetic — see _dispatch
    "timeline.list": ("timeline.list", True),
    "timeline.current": ("timeline.current", False),
    "timeline.inspect": ("timeline.inspect", True),  # synthetic
    "timeline.create": ("timeline.create", True),
    "timeline.ensure": ("timeline.ensure", True),
    "timeline.switch": ("timeline.set_current", True),
    "render.queue": ("render.queue", True),
    "render.presets": ("render.presets", True),
    "render.formats": ("render.formats", True),
    "render.codecs": ("render.codecs", True),
    "render.submit": ("render.submit", True),
    "render.is_rendering": ("render.is_rendering", True),
    "render.stop": ("render.stop", True),
    "render.clear": ("render.clear", True),
}


def _serialize(value: Any) -> Any:
    """Convert a wrapped object to a plain JSON-able value."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if hasattr(value, "inspect"):
        return _serialize(value.inspect())
    if hasattr(value, "to_dict"):
        return _serialize(value.to_dict())
    return str(value)


def _dispatch(resolve: Resolve, method: str, params: Any) -> Any:
    """Dispatch a single request against a live :class:`Resolve` instance."""
    if method not in _METHODS:
        raise errors.DvrError(
            f"Unknown method {method!r}.",
            fix="See `dvr serve methods` for the allow-list.",
        )

    # Synthetic methods that don't map cleanly to a single attribute.
    if method == "timeline.inspect":
        target = resolve.timeline.current
        if target is None:
            raise errors.TimelineError("No current timeline.")
        return target.inspect()
    if method == "app.page.set":
        if isinstance(params, dict):
            page_name = params.get("name", "")
        elif isinstance(params, list) and params:
            page_name = params[0]
        else:
            page_name = ""
        resolve.app.page = page_name
        return resolve.app.page
    if method == "project.save":
        current = resolve.project.current
        if current is None:
            raise errors.ProjectError("No project is currently loaded.")
        current.save()
        return {"saved": current.name}

    # Generic dispatch: walk the dotted path against ``resolve``.
    path, callable_ = _METHODS[method]
    obj: Any = resolve
    for part in path.split("."):
        obj = getattr(obj, part)

    if not callable_:
        return obj  # property read

    if params is None:
        result = obj()
    elif isinstance(params, dict):
        result = obj(**params)
    elif isinstance(params, list):
        result = obj(*params)
    else:
        result = obj(params)
    return result


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        for raw_line in self.rfile:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError as exc:
                self._reply({"id": None, "ok": False, "error": {"message": f"bad JSON: {exc}"}})
                continue
            req_id = req.get("id") or str(uuid.uuid4())
            method = req.get("method", "")
            params = req.get("params")
            try:
                # Get a live Resolve handle on every request — this hides
                # Resolve quit/restart cycles from the client.
                peer = self.server.get_resolve()
                result = _dispatch(peer, method, params)
                self._reply({"id": req_id, "ok": True, "result": _serialize(result)})
            except errors.DvrError as exc:
                self._reply({"id": req_id, "ok": False, "error": exc.to_dict()})
            except Exception as exc:
                # If the underlying Resolve handle has gone stale (RPC
                # exception, broken pipe, etc.), drop the cached connection
                # so the next request reconnects.
                self.server.invalidate_resolve()
                self._reply(
                    {
                        "id": req_id,
                        "ok": False,
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    }
                )

    def _reply(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload) + "\n"
        self.wfile.write(line.encode("utf-8"))
        with suppress(BrokenPipeError):
            self.wfile.flush()


# ``ThreadingUnixStreamServer`` is POSIX-only. Importing the module on
# Windows must still succeed (the CLI imports the daemon module
# unconditionally to register subcommands), so we resolve the base
# class lazily and fall back to a tiny stub. Actually invoking
# ``serve()`` on Windows raises with a clear message before reaching
# any code that needs the real base class.
_THREADING_UNIX_STREAM_SERVER: Any = getattr(socketserver, "ThreadingUnixStreamServer", object)


class _Server(_THREADING_UNIX_STREAM_SERVER):  # type: ignore[misc, valid-type, unused-ignore]
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self, path: str, *, auto_launch: bool = True, connect_timeout: float = 30.0
    ) -> None:
        super().__init__(path, _Handler)
        self._auto_launch = auto_launch
        self._connect_timeout = connect_timeout
        self._resolve: Resolve | None = None
        self._lock = threading.Lock()

    def get_resolve(self) -> Resolve:
        """Return a live :class:`Resolve`, reconnecting if the previous one died."""
        with self._lock:
            if self._resolve is not None:
                # Cheap liveness check — GetVersionString is fast and fails
                # fast on a stale handle.
                try:
                    _ = self._resolve.app.version
                    return self._resolve
                except Exception:
                    logger.warning("cached Resolve handle is stale; reconnecting")
                    self._resolve = None

            self._resolve = Resolve(auto_launch=self._auto_launch, timeout=self._connect_timeout)
            return self._resolve

    def invalidate_resolve(self) -> None:
        """Drop the cached Resolve handle so the next request reconnects."""
        with self._lock:
            self._resolve = None


def serve(*, auto_launch: bool = True, timeout: float = 30.0) -> None:
    """Run the daemon in the foreground until interrupted."""
    if sys.platform == "win32":
        raise errors.DvrError(
            "The daemon mode does not currently support Windows.",
            fix="Use the in-process Python library on Windows.",
        )

    sock = socket_path()
    if sock.exists():
        # Stale socket from a previous run? Try probing.
        if _ping_existing():
            raise errors.DvrError(
                f"A dvr daemon is already running at {sock}.",
                fix="Run `dvr serve stop` first.",
            )
        sock.unlink()

    server = _Server(str(sock), auto_launch=auto_launch, connect_timeout=timeout)
    os.chmod(sock, 0o600)
    pid_path().write_text(str(os.getpid()))

    # Eagerly establish the first connection so problems surface up-front
    # rather than on first client request.
    try:
        resolve = server.get_resolve()
        logger.info("Resolve %s connected; daemon listening on %s", resolve.app.version, sock)
    except errors.DvrError as exc:
        logger.warning(
            "could not connect to Resolve at startup; will retry on first request: %s", exc
        )

    try:
        server.serve_forever()
    finally:
        server.server_close()
        with suppress(FileNotFoundError):
            sock.unlink()
        with suppress(FileNotFoundError):
            pid_path().unlink()


def _ping_existing(timeout: float = 0.5) -> bool:
    """Return True if a daemon is responsive at the conventional socket."""
    sock = socket_path()
    if not sock.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:  # type: ignore[attr-defined, unused-ignore]
            s.settimeout(timeout)
            s.connect(str(sock))
            s.sendall(b'{"id":"ping","method":"app.version"}\n')
            data = s.recv(4096)
            return b'"ok": true' in data or b'"ok":true' in data
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class Client:
    """Synchronous client for a running daemon."""

    def __init__(self, path: Path | None = None, timeout: float = 60.0) -> None:
        self._path = path or socket_path()
        self._timeout = timeout

    def call(self, method: str, params: Any = None) -> Any:
        if not self._path.exists():
            raise errors.ConnectionError(
                "No dvr daemon is running.",
                fix="Run `dvr serve start` first, or omit the daemon and use direct mode.",
                state={"socket": str(self._path)},
            )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:  # type: ignore[attr-defined, unused-ignore]
            s.settimeout(self._timeout)
            s.connect(str(self._path))
            req = {"id": str(uuid.uuid4()), "method": method, "params": params}
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
            response = json.loads(data.decode("utf-8"))
        if not response.get("ok", False):
            err = response.get("error", {})
            raise errors.DvrError(
                err.get("message", "daemon call failed"),
                cause=err.get("cause"),
                fix=err.get("fix"),
                state=err.get("state", {}),
            )
        return response.get("result")


def stop_daemon() -> bool:
    """Stop a running daemon by sending SIGTERM. Returns True if one was stopped."""
    pid_file = pid_path()
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink()
        return False

    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink()
        with suppress(FileNotFoundError):
            socket_path().unlink()
        return False
    return True


def status() -> dict[str, Any]:
    """Return ``{"running": bool, "pid": int | None, "socket": str}``."""
    pid_file = pid_path()
    sock = socket_path()
    if not pid_file.exists() or not sock.exists():
        return {"running": False, "pid": None, "socket": str(sock)}
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return {"running": False, "pid": None, "socket": str(sock)}

    # Cheap liveness check.
    try:
        os.kill(pid, 0)
        running = _ping_existing(timeout=1.0)
    except ProcessLookupError:
        running = False
    return {"running": running, "pid": pid, "socket": str(sock)}


__all__ = ["Client", "serve", "socket_path", "status", "stop_daemon"]
