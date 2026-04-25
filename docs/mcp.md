# MCP server

The Model Context Protocol server exposes `dvr` as typed tools for LLM agents — Claude, Cursor, or any other MCP-compatible client. LLMs call structured tools instead of parsing shell output.

## Install and run

```bash
pip install "dvr[mcp]"
dvr mcp serve
```

The server uses **stdio transport** by default. Clients spawn it as a subprocess and speak MCP over stdin/stdout. Configure your client to launch it:

=== "Claude Desktop"
    Add to `claude_desktop_config.json`:
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

## Available tools

| Tool | Purpose |
|------|---------|
| `ping` | Verify connection. Returns version. |
| `inspect` | One-call snapshot of app + project + timeline. |
| `page_set` | Switch Resolve page. |
| `project_list` / `project_ensure` / `project_current` / `project_save` | Project ops. |
| `timeline_list` / `timeline_inspect` / `timeline_ensure` / `timeline_switch` | Timeline ops. |
| `media_inspect` / `media_bins` / `media_ls` / `media_import` | Media pool. |
| `render_queue` / `render_presets` / `render_formats` / `render_codecs` | Render config. |
| `render_submit` / `render_status` / `render_stop` | Render control. |
| `interchange_export` | Export EDL / AAF / FCPXML / OTIO / etc. |

Each tool has an explicit JSON schema, so agents see exactly what arguments are accepted before they call.

## Errors are first-class

When a tool fails, the response carries the structured `DvrError`:

```json
{
  "error": {
    "type": "TimelineError",
    "message": "No timeline is currently loaded.",
    "cause": "GetCurrentTimeline returned None.",
    "fix": "Switch or create a timeline first.",
    "state": {"project": "MyShow_207"}
  }
}
```

Agents can branch on `error.type` and recover via the suggested `fix`. See [Errors and diagnostics](concepts/errors.md) for the field shapes.

## Connection caching

The MCP server connects to Resolve lazily on the first tool call and reuses that connection for the rest of the session. No per-tool handshake.

## Designing prompts that work well

- **Read before mutating.** Have the agent call `inspect` before deciding what to do. One read replaces a chain of getters.
- **Use idempotent tools.** `project_ensure` and `timeline_ensure` are safer than create/load pairs in agent code paths.
- **Stream renders.** `render_submit` returns a job ID; `render_status` polls it. The client can show progress to the user.
