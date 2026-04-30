# dvr

<p align="center">
  <img src="assets/logo.png" alt="dvr logo" width="160">
</p>

**The missing CLI and Python library for DaVinci Resolve.**
Declarative. Scriptable. LLM-friendly. No more silent `None` returns.

=== "Homebrew"
    ```bash
    brew install mhadifilms/tap/dvr
    ```

=== "PyPI"
    ```bash
    pip install dvr
    # or: pipx install dvr
    # or: uv tool install dvr
    ```

```bash
$ dvr timeline inspect
{
  "name": "Edit_v2",
  "fps": 24.0,
  "duration_frames": 86400,
  "tracks": {
    "video": [{"index": 1, "name": "V1", "clips": 1, "enabled": true}, ...],
    "audio": [{"index": 1, "name": "A1", "clips": 4, "subtype": "stereo"}, ...]
  },
  "marker_count": 12
}
```

## Why dvr exists

DaVinci Resolve has a powerful Python scripting API. It's also painful:

- **Silent failures everywhere.** `AddRenderJob()` returns `None` on success or failure — good luck.
- **String-keyed settings** with undocumented valid values.
- **No batch operators.** You loop everything.
- **macOS connection footguns.** Resolve binds to LAN IP; vanilla `scriptapp('Resolve')` returns `None`.
- **Chain navigation.** Every `.Get*()` call can return `None`. One typo and you're traversing nothing.
- **20+ export formats** behind magic enum constants.

`dvr` wraps the API with a clean object model, idempotent operations, decoded errors, structured I/O, and a CLI that's pleasant for humans *and* parseable by LLM agents.

## Three ways to use it

=== "Python"
    ```python
    from dvr import Resolve

    r = Resolve()  # auto-connects, handles macOS LAN-IP quirk

    with r.project.use("MyShow"):
        tl = r.timeline.current
        print(tl.inspect())                      # one call, full state

        bad = tl.clips().where(lambda c: c.duration < 12)
        for clip in bad:
            clip.add_marker(color="red", note="too short")

        job = r.render.submit(target_dir="/Volumes/out", preset="delivery")
        job.wait()                               # blocks with progress
        print(job.output_path)
    ```

=== "CLI"
    ```bash
    dvr project ensure MyShow
    dvr timeline inspect | jq '.tracks.video[].clips'
    dvr render submit --target-dir /Volumes/out --preset delivery --wait --stream
    # newline-delimited JSON status events:
    # {"job_id": "abc", "status": "rendering", "pct": 12, "eta_s": 240}
    # {"job_id": "abc", "status": "complete", "output_path": "/Volumes/out/MyShow.mov"}
    ```

=== "MCP (LLM agents)"
    ```bash
    pip install dvr
    dvr mcp serve
    ```

    Now any MCP-compatible LLM client can drive Resolve through typed tools instead of shell commands.

## Five things that make it fundamentally better than the raw API

1. **One [`inspect()`](concepts/inspect.md) call replaces ten API calls.** Full structured state in a single round-trip.
2. **[Idempotent operations.](concepts/idempotency.md)** `project.ensure()`, `timeline.ensure()`, `bin.ensure()` — re-run anything safely.
3. **[Decoded errors.](concepts/errors.md)** Every failure carries `cause`, `fix`, and `state`. No more `None`.
4. **[Declarative specs.](spec.md)** `dvr apply project.dvr.yaml` reconciles state. *kubectl apply* for DaVinci.
5. **[Persistent connection.](daemon.md)** `dvr serve` keeps Resolve warm — sequential commands run in <100 ms.

## Requirements

- Python 3.10+
- **DaVinci Resolve Studio 18.5+** — external scripting is a Studio-only feature. Blackmagic sells Studio as a $295 perpetual license or through [Blackmagic Cloud](https://www.blackmagicdesign.com/products/davinciresolve) at $30/month per seat. The free edition of Resolve cannot be scripted from outside the app (restricted in v19.1+).
- macOS, Windows, or Linux

## Status

Stable from 1.0. Breaking changes ship with a deprecation cycle and a major version bump; new features land as minor releases. See the [CHANGELOG](https://github.com/mhadifilms/dvr/blob/main/CHANGELOG.md).

## License

MIT — see the [LICENSE](https://github.com/mhadifilms/dvr/blob/main/LICENSE).

---

> `dvr` is an independent open-source project. It is **not affiliated with, endorsed by, or sponsored by Blackmagic Design**. "DaVinci" and "DaVinci Resolve" are trademarks of Blackmagic Design Pty Ltd.
