<p align="center">
  <img src="https://raw.githubusercontent.com/mhadifilms/dvr/main/docs/assets/logo.png" alt="dvr logo" width="140">
</p>

# dvr

[![PyPI version](https://img.shields.io/pypi/v/dvr?color=blue)](https://pypi.org/project/dvr/)
[![Python versions](https://img.shields.io/pypi/pyversions/dvr.svg)](https://pypi.org/project/dvr/)
[![CI](https://github.com/mhadifilms/dvr/actions/workflows/ci.yml/badge.svg)](https://github.com/mhadifilms/dvr/actions/workflows/ci.yml)
[![Docs](https://github.com/mhadifilms/dvr/actions/workflows/docs.yml/badge.svg)](https://mhadifilms.github.io/dvr/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**The missing CLI and Python library for DaVinci Resolve.**

Declarative. Scriptable. LLM-friendly. No more silent `None` returns.

```bash
pip install dvr
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
  "markers": [...],
  "color": {"science": "DaVinci YRGB Color Managed v2", "input": "Rec.2020", ...}
}
```

---

## Why this exists

DaVinci Resolve has a powerful Python scripting API. It's also painful:

- **Silent failures everywhere.** `AddRenderJob()` returns `None` on success or failure — good luck.
- **String-keyed settings** with undocumented valid values.
- **No batch operators.** You loop everything.
- **macOS connection footguns.** Resolve binds to LAN IP; vanilla `scriptapp('Resolve')` returns `None`.
- **Chain navigation.** Every `.Get*()` can return `None`. One typo and you're traversing nothing.
- **20+ export formats** behind magic enum constants.

`dvr` wraps the API with a clean object model, idempotent operations, decoded errors, structured I/O, and a CLI that's pleasant for humans *and* parseable by LLM agents.

## Three ways to use it

### 1. Python library

```python
from dvr import Resolve

r = Resolve()  # auto-connects, handles macOS LAN-IP quirk

with r.project.use("MyShow"):
    tl = r.timeline.current
    print(tl.inspect())                      # one call, full state

    # Query language operates on inspected state
    bad = tl.clips.where(lambda c: c.duration < 12)
    for clip in bad:
        clip.add_marker(color="red", note="too short")

    job = r.render.submit(preset="delivery")
    job.wait()                               # blocks with progress
    print(job.output_path)
```

### 2. CLI

```bash
$ dvr project ensure MyShow --color rec2020_pq_4000 --fps 24
$ dvr timeline inspect | jq '.tracks.video[].clips'
$ dvr render submit --preset delivery --wait --stream
{"job_id": "abc", "status": "rendering", "pct": 12, "eta_s": 240}
{"job_id": "abc", "status": "rendering", "pct": 24, "eta_s": 210}
{"job_id": "abc", "status": "complete", "output": "/path/out.mov"}
```

### 3. MCP server (for LLM agents)

```bash
$ pip install "dvr[mcp]"
$ dvr mcp install-claude     # one-shot Claude Desktop setup
$ dvr mcp serve              # or run the server yourself
$ dvr mcp tools              # introspect the 39+ typed tools
```

LLM agents call typed tools directly — no shell parsing, no silent failures. Tools that don't need a live Resolve (`version`, `doctor`, static `schema` topics) work even when Resolve isn't running, so first-time setup is instant. See [docs/mcp.md](docs/mcp.md).

## Five things that make it fundamentally better than the raw API

1. **One `inspect()` call replaces ten API calls.** Full structured state in a single round-trip.
2. **Idempotent operations.** `project.ensure()`, `timeline.ensure()`, `bin.ensure()` — re-run anything safely.
3. **Decoded errors.** Every failure carries `cause`, `fix`, and `state`. No more `None`.
4. **Declarative specs.** `dvr apply project.dvr.yaml` reconciles state. `kubectl apply` for DaVinci.
5. **Persistent connection.** `dvr serve` keeps Resolve warm — sequential commands run in <100ms.

## Install

| Channel | Command |
|---------|---------|
| **Homebrew** (macOS / Linux) | `brew install mhadifilms/tap/dvr` |
| **PyPI** | `pip install dvr` |
| **pipx** | `pipx install dvr` |
| **uv** | `uv tool install dvr` |
| **From source** | `git clone https://github.com/mhadifilms/dvr && cd dvr && pip install -e ".[dev]"` |

### Optional extras

```bash
pip install "dvr[mcp]"            # MCP server for LLM agents
pip install "dvr[docs]"           # docs site dependencies
pip install "dvr[dev]"            # dev (ruff, mypy, pytest)
```

### Homebrew details

`dvr` ships via my personal tap at [`mhadifilms/homebrew-tap`](https://github.com/mhadifilms/homebrew-tap). The recommended pattern is to tap once, then use the bare command:

```bash
brew tap mhadifilms/tap
brew install dvr            # works after the tap is installed
```

Or, if you only ever want to install once and don't care about updates:

```bash
brew install mhadifilms/tap/dvr
```

> **`brew install dvr` says "no formula"?** That's expected if you haven't tapped yet. Homebrew only searches `homebrew/core` by default; our formula lives in our personal tap. Run `brew tap mhadifilms/tap` and try again.

### Requirements

- **Python 3.10+** (matches Resolve's embedded Python on current versions)
- **DaVinci Resolve Studio 18.5+** — external scripting is a Studio-only feature. Blackmagic Design sells Studio as a one-time $295 perpetual license or via [Blackmagic Cloud](https://www.blackmagicdesign.com/products/davinciresolve) at $30/month per seat.
- **macOS, Windows, or Linux**

`dvr` auto-discovers Resolve's scripting library on each platform. No environment variables needed for typical installs.

> The free edition of DaVinci Resolve cannot be scripted from outside the app (Blackmagic restricted this in v19.1+). If you're evaluating `dvr` without Studio, use `--dry-run` flags on `apply` and explore the schema/inspection commands — they work without a live connection.

## Status

Stable from 1.0. Breaking changes ship with a deprecation cycle and a major version bump; new features land as minor releases. See [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and pull requests welcome. The project's API surface is large; contributions covering edge cases on Windows / Linux are especially valuable. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and conventions.

---

> `dvr` is an independent open-source project. It is **not affiliated with, endorsed by, or sponsored by Blackmagic Design**. "DaVinci" and "DaVinci Resolve" are trademarks of Blackmagic Design Pty Ltd.
