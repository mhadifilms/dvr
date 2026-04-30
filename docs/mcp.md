# MCP server

The Model Context Protocol server exposes `dvr` as typed tools for LLM agents — Claude, Cursor, or any other MCP-compatible client. LLMs call structured tools instead of parsing shell output.

## Install

```bash
pip install "dvr[mcp]"
```

The server uses **stdio transport** by default — clients spawn `dvr mcp serve` as a subprocess and speak MCP over stdin/stdout.

## Configure a client (one command)

```bash
dvr mcp install-claude   # Claude Desktop
dvr mcp install-cursor   # Cursor (~/.cursor/mcp.json)
```

These commands write the absolute path to `dvr` into the client's MCP config, preserving any other settings already there. Restart the client and you'll see the `dvr` tools available.

Useful flags:

| Flag | Effect |
|------|--------|
| `--name <name>` | Register under a name other than `dvr` (e.g. `dvr-prod`). |
| `--enable-eval` | Set `DVR_MCP_ENABLE_EVAL=1` in the server's env so the `eval` tool is callable. |
| `--no-launch` | Pass `--no-launch` to `dvr mcp serve` so it never auto-launches Resolve. |
| `--dry-run` | Print the resulting config without writing it. |
| `--force` | Overwrite an existing entry of the same name. |
| `--config <path>` | Use a custom config file instead of the platform default. |

For any client that uses the standard `mcpServers` JSON shape, use the generic installer:

```bash
dvr mcp install /path/to/mcp.json
```

If you'd rather edit the config by hand:

=== "Claude Desktop"
    Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows), or `~/.config/Claude/claude_desktop_config.json` (Linux):
    ```json
    {
      "mcpServers": {
        "dvr": {
          "command": "dvr",
          "args": ["mcp", "serve"]
        }
      }
    }
    ```

=== "Cursor / Continue / others"
    Set the MCP server command to `dvr mcp serve`. Most clients accept the same JSON shape.

## Inspect the available tools

```bash
dvr mcp tools           # one-line summaries
dvr mcp tools --detail  # full descriptions and JSON schemas
```

## Available tools

### Setup and diagnostics (no Resolve required)

| Tool | Purpose |
|------|---------|
| `version` | Return dvr / Python / platform version plus bundled MCP branding assets. |
| `doctor` | Diagnose the dvr → Resolve setup (paths, env, process running) plus bundled MCP branding assets. Pass `probe=true` to also try a live connection. |
| `reconnect` | Drop the cached Resolve connection and reconnect. |
| `schema` | Catalog of valid setting keys, codecs, properties (some topics are static). |
| `snapshot_list` | List on-disk snapshots, newest first. |

### Live ops

| Tool | Purpose |
|------|---------|
| `ping` | Verify connection. Returns version. |
| `inspect` | One-call snapshot of app + project + timeline. |
| `page_get` / `page_set` | Read or switch the current page. |
| `project_list` / `project_ensure` / `project_current` / `project_settings_get` / `project_save` / `project_delete` | Project ops. |
| `timeline_list` / `timeline_inspect` / `timeline_ensure` / `timeline_switch` / `timeline_rename` / `timeline_delete` / `timeline_clear` | Timeline ops. |
| `timeline_append` | Append media to explicit timeline tracks (`track_index`, `record_frame`, source in/out). Non-default tracks require explicit `record_frame` per item. |
| `marker_add` | Add a marker at a frame on a timeline. |
| `clip_where` | Filter timeline items by safe declarative fields (duration, name, track type). |
| `media_inspect` / `media_bins` / `media_ls` / `media_import` | Media pool. |
| `media_scan` | Scan a filesystem folder for importable video/audio files, skipping hidden AppleDouble files by default. |
| `media_bin_ensure` / `media_bin_delete` / `media_move` | Create/delete nested bins and move media-pool clips without breaking timelines. Slash paths like `Picture/Plates` are accepted consistently. |
| `render_queue` / `render_presets` / `render_formats` / `render_codecs` | Render config. |
| `render_submit` / `render_status` / `render_stop` / `render_clear` | Render control. |
| `interchange_export` | Export EDL / AAF / FCPXML / OTIO / etc. |
| `diff_timelines` / `diff_to_spec` | Structured diffs. |
| `apply_spec` | Reconcile live state to a YAML/JSON spec (with optional `dry_run` and `continue_on_error`). |
| `snapshot_save` / `snapshot_restore` | Capture/restore project state. |
| `lint` | Pre-flight validation. |
| `eval` | Power-user Python eval. **Disabled** unless `DVR_MCP_ENABLE_EVAL=1`. |

Each tool has an explicit JSON schema, so agents see exactly what arguments are accepted before they call.

The MCP surface intentionally exposes reusable editing primitives rather than
show-specific pipeline commands. Agents can combine `media_scan`,
`media_import`, `media_bin_ensure`, `media_move`, and `timeline_append` to build
custom ingest or assembly workflows while each step remains inspectable and
recoverable.

## Errors are first-class

When a tool fails, the response carries the structured `DvrError`:

```json
{
  "error": {
    "type": "TimelineError",
    "message": "No timeline is currently loaded.",
    "cause": "GetCurrentTimeline returned None.",
    "fix": "Switch or create a timeline first.",
    "state": {"project": "MyShow"}
  }
}
```

Agents can branch on `error.type` and recover via the suggested `fix`. See [Errors and diagnostics](concepts/errors.md) for the field shapes.

## Connection caching

The MCP server connects to Resolve lazily on the first tool call that needs it, then reuses that connection for the rest of the session. Tools that don't need Resolve (`version`, `doctor` without `probe=true`, `schema` for static topics, `snapshot_list`) never trigger a connection — they're safe to call at startup.

If Resolve was relaunched or external scripting was just enabled, call `reconnect` to drop the stale handle.

## The `eval` escape hatch

The `eval` tool runs an arbitrary Python expression with `r = Resolve()`, `project`, `timeline`, and `dvr` already bound. It is **off by default** because it bypasses every typed boundary. Enable it with:

```bash
dvr mcp install-claude --enable-eval     # set DVR_MCP_ENABLE_EVAL=1 in the server env
# or, manually, run with: DVR_MCP_ENABLE_EVAL=1 dvr mcp serve
```

## Designing prompts that work well

- **Read before mutating.** Have the agent call `inspect` before deciding what to do. One read replaces a chain of getters.
- **Use idempotent tools.** `project_ensure` and `timeline_ensure` are safer than create/load pairs in agent code paths.
- **Use `doctor` when debugging.** It returns instantly and tells you whether the scripting library is found, env vars are set, and Resolve is running — without trying a long connection.
- **Stream renders.** `render_submit` returns a job ID; `render_status` polls it. The client can show progress to the user.
