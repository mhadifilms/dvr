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
dvr project   list | current | ensure | create | load | delete | save | export | import | generate-speech
dvr timeline  list | current | inspect | ensure | create | switch | delete | add-title | subtitles
dvr media     inspect | bins | ls | mkbin | import | relink | storage
dvr clip      ls | inspect | set | transform | crop | composite | retime | reset | text | capabilities
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

## Clip editing

`dvr clip set` remains the raw property escape hatch, but the common
documented `TimelineItem.SetProperty` controls also have ergonomic commands:

```bash
dvr clip transform --where "track_index == 2" --pan 40 --zoom 1.1
dvr clip crop --where "track_index == 2" --top 120 --bottom 120
dvr clip composite --where "track_index == 2" --mode multiply --opacity 80
dvr clip retime --where "duration > 120" --process optical-flow
dvr clip reset transform crop --where "name == 'plate.mov'"
```

Use `dvr clip capabilities` and `dvr schema clip-properties` to inspect the
exact Resolve-supported property surface.

## Text & titles

Insert a Fusion title (defaults to the built-in `Text+`) and style it in one
command. Colors accept hex (`#ffcc00`), CSS-ish names (`white`), or comma-separated
`r,g,b`. Sizes are Text+ relative units (~0.05–0.2):

```bash
dvr timeline add-title --text "OPENING" --font "Open Sans" --size 0.12 \
  --color "#ffcc00" --align center --at "01:00:02:00"
```

Re-style existing Text+ titles already on the timeline (only clips carrying a
Text+ tool are touched; others are reported as skipped):

```bash
dvr clip text --where "name == 'Text+'" --text "REVISED" --color white
dvr clip text -t video --font "Open Sans" --size 0.1 --align center
```

Generate spoken audio from text, with full voice controls:

```bash
dvr project generate-speech --text "Welcome back." --voice "Female 1" \
  --speed 1.0 --pitch 0 --track 2
```

Auto-caption a timeline from its audio (Whisper, Studio):

```bash
dvr timeline subtitles --language en --chars-per-line 42
```

## Errors

When a command fails, the CLI exits with code `1` and writes a structured error to stderr (in the chosen output format). See [Errors and diagnostics](concepts/errors.md) for the field layout.
