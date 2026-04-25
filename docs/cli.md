# CLI reference

The `dvr` CLI is a thin wrapper around the Python library. Every command produces structured output (JSON when piped, a Rich table when interactive).

## Top-level

```text
dvr inspect           One-call snapshot of Resolve, current project, and current timeline.
dvr ping              Verify the connection. Prints version on success.
dvr page [NAME]       Read or set the current Resolve page.
dvr plan FILE         Show the actions `dvr apply` would take.
dvr apply FILE        Reconcile a spec against the live Resolve state.
```

## Domains

```text
dvr project   list | current | ensure | create | load | delete | save | export | import
dvr timeline  list | current | inspect | ensure | create | switch | delete
dvr media     inspect | bins | ls | mkbin | import | relink | storage
dvr render    queue | presets | formats | codecs | submit | status | watch | stop | clear
dvr serve     start | stop | status | methods                   (daemon mode)
dvr mcp       serve                                              (MCP server for LLM agents)
```

## Global flags

| Flag | Purpose |
|------|---------|
| `--format`, `-f` | Output: `json` (default when piped) `\|` `table` (default when TTY) `\|` `yaml`. |
| `--no-launch` | Don't auto-launch DaVinci Resolve if it isn't running. |
| `--timeout SECS` | Seconds to wait for Resolve to be reachable. Default 30. |
| `--version`, `-V` | Print the `dvr` version and exit. |

Set `DVR_FORMAT` in your environment to a permanent default.

## Output

JSON is always one well-formed object per command (no trailing newlines, no ANSI). `dvr ... | jq` Just Works.

```bash
dvr timeline inspect | jq '.tracks.video[] | select(.clip_count > 0)'
```

Tables are only rendered when stdout is a TTY. To force a format:

```bash
dvr timeline list --format table
dvr render queue --format yaml
```

## Render streaming

`render submit --wait --stream` and `render watch` emit newline-delimited JSON. Each line is a status event:

```json
{"type": "progress", "job_id": "abc", "status": "Rendering", "percent": 12, "eta_s": 240}
{"type": "progress", "job_id": "abc", "status": "Rendering", "percent": 24, "eta_s": 210}
{"type": "complete", "job_id": "abc", "output_path": "/Volumes/out/MyShow.mov", "time_s": 380}
```

This works well with `jq`, `xargs`, or any stream processor.

## Errors

When a command fails, the CLI exits with code `1` and writes a structured error to stderr (in the chosen output format). See [Errors and diagnostics](concepts/errors.md) for the field layout.
