# MCP server

The Model Context Protocol server exposes `dvr` as typed tools for LLM agents — Claude, Cursor, or any other MCP-compatible client. LLMs call structured tools instead of parsing shell output.

The server uses the MCP Python SDK 2.x protocol models and low-level callback
API while preserving explicit JSON schemas for every `dvr` tool.

## Install

```bash
pip install dvr
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

## Tool profiles and `tool_search`

`dvr` ships more than ninety tools. Listing all of them costs roughly 9,000
tokens of context in *every* request, most of it schemas for tools a given
session never calls.

By default the server lists a `core` profile — the operations a session
reaches for first — and leaves the rest to `tool_search`:

| Profile | Tools listed | Tool-list payload |
|---------|--------------|-------------------|
| `core` (default) | 36 | ~13 KB |
| `full` | 94 | ~38 KB |

**Every tool stays callable regardless of profile.** Narrowing the listing
narrows what the agent sees up front, never what the server can do. An agent
that needs something unlisted calls `tool_search` and gets the full schema
back, then calls the tool by name:

```json
{"name": "tool_search", "arguments": {"query": "dctl"}}
```

Set `DVR_MCP_PROFILE=full` to restore the pre-1.7 behavior of listing
everything up front.

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
| `clip_set_properties` / `clip_transform` / `clip_crop` / `clip_reset` / `clip_capabilities` | Set documented static timeline-item controls and discover unsupported transition/keyframe capabilities without using `eval`. |
| `timeline_add_title` | Insert a (Fusion) title and style its text — string, font, style, size, color (hex/name/`[r,g,b]`), opacity, tracking, line spacing, position, and alignment. |
| `clip_set_text` | Re-style Text+ titles selected by safe filters; non-text clips are reported as skipped. |
| `timeline_create_subtitles` | Generate subtitles from a timeline's audio (Whisper, Studio) with language / chars-per-line / line-break / preset. |
| `project_generate_speech` | Text-to-speech to an audio clip, including custom voice, speed, variation, pitch, generation ID, filename, and timeline placement (Resolve 21+, Studio). |
| `media_inspect` / `media_bins` / `media_ls` / `media_import` | Media pool. |
| `media_scan` | Scan a filesystem folder for importable video/audio files, skipping hidden AppleDouble files by default. |
| `media_bin_ensure` / `media_bin_delete` / `media_move` | Create/delete nested bins and move media-pool clips without breaking timelines. Slash paths like `Picture/Plates` are accepted consistently. |
| `timeline_assemble` | **Workflow tool:** ensure a timeline, import media by path, and append every item in order — a rough cut in one call. |
| `color_inspect` | Node graph (labels, tools, per-node LUTs), grade versions, and color group for filtered clips. |
| `color_set_cdl` | Apply slope / offset / power / saturation to a node across a clip selection. |
| `color_node_lut` | Read or set the LUT on a color node. |
| `color_export_lut` | Export a clip's grade as a LUT (17 / 33 / 65 / `vlt`). |
| `color_versions` | List / add / load / delete / rename grade versions. |
| `color_copy_grades` | Copy one clip's grade onto the rest of a selection. |
| `color_reset` | Reset every color node on a selection. |
| `dctl_list` / `dctl_read` / `dctl_write` / `dctl_delete` | Manage `.dctl` files in Resolve's LUT directory. Source is validated before it is written. |
| `lut_list` / `lut_generate` / `lut_delete` | Manage LUT files, including generating a `.cube` from a transform expression. |
| `tool_search` | Find tools not listed under the current profile and return their schemas. |
| `render_queue` / `render_presets` / `render_formats` / `render_codecs` | Render config. |
| `render_submit` / `render_status` / `render_stop` / `render_clear` | Render control. |
| `render_wait` | Block until a job finishes (or fails / times out) and return its final status — prefer this over polling `render_status`. |
| `interchange_export` | Export EDL / AAF / FCPXML / OTIO / etc. |
| `diff_timelines` / `diff_to_spec` | Structured diffs. |
| `apply_spec` | Reconcile live state to a YAML/JSON spec. Supports `dry_run`, `continue_on_error`, `transactional` (snapshot + auto-rollback), and `verify` (read-back checks). |
| `spec_export` | Build a spec from live project state — adopt an existing project into spec-managed workflows. |
| `snapshot_save` / `snapshot_restore` | Capture/restore project state. |
| `lint` | Pre-flight validation. |
| `eval` | Restricted Python eval — no imports, no dunder access. **Disabled** unless `DVR_MCP_ENABLE_EVAL=1`. |
| `eval_unsafe` | Unrestricted Python eval, including host access. **Disabled** unless `DVR_MCP_ENABLE_EVAL_UNSAFE=1`. |

Each tool has an explicit JSON schema, so agents see exactly what arguments are accepted before they call.

The MCP surface intentionally exposes reusable editing primitives rather than
show-specific pipeline commands. Agents can combine `media_scan`,
`media_import`, `media_bin_ensure`, `media_move`, and `timeline_append` to build
custom ingest or assembly workflows while each step remains inspectable and
recoverable.

## Resources — read state, don't guess it

Alongside tools, the server exposes live state as MCP **resources** (JSON):

| URI | Contents |
|-----|----------|
| `dvr://inspect` | One-call snapshot of Resolve, current project, current timeline. |
| `dvr://project/current` | Current project inspect. |
| `dvr://timeline/current` | Full current-timeline inspect: tracks, items, markers. |
| `dvr://media/bins` | The current project's bin tree. |
| `dvr://render/queue` | Jobs in the render queue. |
| `dvr://doctor` | Static setup diagnostics (no Resolve needed). |
| `dvr://schema/<topic>` | Static catalogs: `settings`, `clip-properties`, `color-presets`, `export-formats`. |

Clients that support resources can attach these to context instead of burning tool calls on state reads.

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

## The `eval` escape hatches

Two tiers, gated separately, both off by default.

`eval` runs a Python expression with `r = Resolve()`, `project`, `timeline`
and `dvr` bound. Imports and dunder attribute access are blocked, so the
expression cannot reach the filesystem, network, or subprocesses. It still
runs against a live Resolve, so it can change the project — the boundary is
against the host, not against Resolve.

```bash
dvr mcp install-claude --enable-eval     # sets DVR_MCP_ENABLE_EVAL=1
# or: DVR_MCP_ENABLE_EVAL=1 dvr mcp serve
```

`eval_unsafe` runs unrestricted Python, with imports, filesystem, network and
subprocesses all reachable. Use it only when the expression genuinely needs
host access, and never on a shared or unattended machine:

```bash
DVR_MCP_ENABLE_EVAL_UNSAFE=1 dvr mcp serve
```

!!! warning "Changed in 1.7.0"

    Before 1.7.0, `eval` described itself as allowing "No imports" while in
    fact passing a plain dictionary to Python's `eval`, into which CPython
    injects the full builtins — so `__import__('subprocess')` worked. The
    restriction is now real, and the unrestricted behavior moved to the
    separately gated `eval_unsafe`.

## Designing prompts that work well

- **Read before mutating.** Have the agent call `inspect` before deciding what to do. One read replaces a chain of getters.
- **Use idempotent tools.** `project_ensure` and `timeline_ensure` are safer than create/load pairs in agent code paths.
- **Use `doctor` when debugging.** It returns instantly and tells you whether the scripting library is found, env vars are set, and Resolve is running — without trying a long connection.
- **Stream renders.** `render_submit` returns a job ID; `render_status` polls it. The client can show progress to the user.
