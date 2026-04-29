# Getting started

## Install

=== "Homebrew (macOS / Linux)"
    ```bash
    brew install mhadifilms/tap/dvr
    ```

=== "PyPI"
    ```bash
    pip install dvr                  # or `pipx install dvr`, or `uv tool install dvr`
    pip install "dvr[mcp]"           # plus the MCP server for LLM agents
    ```

=== "From source"
    ```bash
    git clone https://github.com/mhadifilms/dvr
    cd dvr
    pip install -e ".[dev]"
    ```

### Requirements

- **Python 3.10+** — matches DaVinci Resolve's embedded Python on current versions
- **DaVinci Resolve Studio 18.5+** — see below
- **macOS, Windows, or Linux**

!!! warning "Resolve Studio is required"
    External scripting is a **Studio-only feature**. Blackmagic restricted the scripting API to the paid version starting in Resolve 19.1 — the free edition's `External scripting using` preference no longer accepts `Local`.

    Studio costs $295 as a perpetual one-time license, or $30/month per seat via [Blackmagic Cloud](https://www.blackmagicdesign.com/products/davinciresolve). If you only have the free edition, `dvr` will raise `ScriptingDisabledError` when trying to connect. The schema and inspection commands still work in `--dry-run` mode for evaluation.

`dvr` auto-discovers Resolve's scripting library on each platform. No environment variables are needed for typical installs. If yours is non-standard, set `RESOLVE_SCRIPT_LIB` to the absolute path of `fusionscript.so` / `fusionscript.dll`.

## Verify the connection

Open DaVinci Resolve, then run:

```bash
dvr ping
```

You should see something like:

```json
{
  "connected": true,
  "version": "20.3.1",
  "product": "DaVinci Resolve Studio"
}
```

If you get a `ScriptingDisabledError`, open Resolve's preferences:

1. **Preferences → System → General**
2. Set **External scripting using** to **Local**
3. Quit and re-launch Resolve

## Your first command

Inspect the current state of Resolve:

```bash
dvr inspect
```

Switch to a page:

```bash
dvr page deliver
```

List your projects:

```bash
dvr project list
```

Create a project (idempotent — safe to re-run):

```bash
dvr project ensure MyShow
```

Get a structured snapshot of the current timeline:

```bash
dvr timeline inspect
```

## Next steps

- [Python library](library.md) — use `dvr` from your own scripts
- [CLI reference](cli.md) — full command list and flags
- [Declarative specs](spec.md) — reconcile state from a YAML file
- [Daemon mode](daemon.md) — keep Resolve warm for fast back-to-back commands
- [MCP server](mcp.md) — let an LLM drive Resolve directly
