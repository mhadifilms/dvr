# Daemon mode

DaVinci Resolve's cold scripting connection takes 2-3 seconds on macOS. For LLM agents and shell scripts that run many sequential commands, that handshake dominates wall-clock time.

`dvr serve` runs once in the background and serves requests over a Unix-domain socket.

## Quick start

```bash
dvr serve start                  # detaches into the background
dvr timeline list                # ~50ms instead of ~2.5s
dvr timeline inspect             # ~50ms
dvr serve stop
```

## Status

```bash
dvr serve status
```

```json
{
  "running": true,
  "pid": 91234,
  "socket": "/Users/you/.cache/dvr/dvr.sock"
}
```

## Wire format

The daemon speaks newline-delimited JSON over its Unix socket. One request per line, one response per line:

```json
{"id": "abc", "method": "timeline.inspect", "params": {}}
```

```json
{"id": "abc", "ok": true, "result": {"name": "MyShow", "fps": 24, ...}}
```

Errors come back as the standard `DvrError.to_dict()` payload:

```json
{"id": "abc", "ok": false, "error": {"type": "TimelineError", "message": "...", "cause": "...", "fix": "...", "state": {}}}
```

## Method allow-list

The daemon dispatches against an explicit allow-list to prevent arbitrary attribute traversal. List it with:

```bash
dvr serve methods
```

Examples: `timeline.inspect`, `project.list`, `project.ensure`, `render.submit`, `render.queue`, `app.page.set`, `app.version`.

## Python client

```python
from dvr.daemon import Client

client = Client()
result = client.call("timeline.inspect")
```

If the daemon isn't running, the client raises `dvr.errors.ConnectionError` with a `fix` pointing to `dvr serve start`.

## Socket location

| Platform | Default socket path |
|----------|--------------------|
| macOS / Linux (no `XDG_RUNTIME_DIR`) | `~/.cache/dvr/dvr.sock` |
| Linux (with `XDG_RUNTIME_DIR`) | `$XDG_RUNTIME_DIR/dvr/dvr.sock` |
| Windows | not currently supported — use the in-process Python library |

The socket is created with mode `0600` — same-user only.

## Lifecycle

The daemon holds exactly one Resolve connection. If Resolve quits, the daemon's connection becomes stale; restart the daemon (`dvr serve stop && dvr serve start`).

For automation, you can detach the daemon from a single shell command:

```bash
dvr serve start --background       # default
dvr serve start --foreground       # blocking, useful for systemd-style supervision
```
