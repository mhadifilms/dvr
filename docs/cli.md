# CLI reference

The `dvr` CLI is a thin wrapper around the Python library. Every command produces structured output (JSON when piped, a Rich table when interactive).

## Top-level

```text
dvr inspect           One-call snapshot of Resolve, current project, and current timeline.
dvr ping              Verify the connection. Prints version on success.
dvr doctor [--probe]  Diagnose the setup: paths, process, env — and connectivity with --probe.
dvr page [NAME]       Read or set the current Resolve page.
dvr plan FILE         Show the actions `dvr apply` would take.
dvr apply FILE        Reconcile a spec against the live Resolve state.
```

## Domains

```text
dvr project   list | current | ensure | create | load | delete | save | export | import | generate-speech
dvr timeline  list | current | inspect | ensure | create | switch | delete | add-title | subtitles
dvr media     inspect | bins | ls | scan | mkbin | import | relink | storage
dvr clip      ls | inspect | set | transform | crop | composite | retime | reset | text | capabilities
dvr color     inspect | cdl | lut get/set/export | version ls/add/load/delete | copy | reset | group
dvr render    queue | presets | formats | codecs | submit | status | watch | stop | clear
dvr lut       root | ls | generate | rm                          (files in Resolve's LUT dir)
dvr dctl      ls | cat | write | validate | rm                   (files in Resolve's LUT dir)
dvr spec      export                                             (adopt live projects into specs)
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

## Daemon forwarding

When a `dvr serve` daemon is running, ordinary commands automatically route through it and reuse its persistent Resolve connection (~50ms instead of ~2.5s per command). `DVR_NO_DAEMON=1` forces local execution; `DVR_DAEMON=auto` auto-spawns the daemon on first use. See [Daemon mode](daemon.md).

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
  --variation 2 --speed 1.0 --pitch 0 --track 2
```

The 21.0.4 motion-deblur surface includes output name/format/codec/profile,
mark-range and source-resolution controls, GPU-memory mode, and H.265 encoder:

```bash
dvr media deblur --clip shot010 --filename shot010_deblur \
  --format mov --codec H265 --encoding-profile Main10 \
  --use-mark-in-out --source-resolution --more-gpu-memory --encoder Native
```

Auto-caption a timeline from its audio (Whisper, Studio):

```bash
dvr timeline subtitles --language en --chars-per-line 42
```

## Diagnostics

`dvr doctor` reports where the Resolve scripting library is expected, whether it exists, whether the Resolve process is running, and the relevant environment variables — without touching Resolve. Add `--probe` to also attempt a live connection:

```bash
dvr doctor            # fast, static
dvr doctor --probe    # additionally connects (a few seconds on macOS)
```

`dvr media scan PATH` previews which files a bulk import would pick up (video/audio by extension; hidden and AppleDouble `._*` files skipped), also without needing Resolve:

```bash
dvr media scan ~/Footage --no-recursive
dvr media scan /Volumes/Card01 | jq '.[].path'
```

## Errors

When a command fails, the CLI exits with code `1` and writes a structured error to stderr (in the chosen output format). This applies to every command — connection failures, missing projects, invalid bins, and so on all render as the same `type` / `message` / `cause` / `fix` / `state` payload instead of a Python traceback. See [Errors and diagnostics](concepts/errors.md) for the field layout.

## Color grading

Selection works exactly like `dvr clip`: `--where` uses the same expression
language and `--track` narrows by track type (color commands default to
video).

```bash
dvr color inspect --where "track_index == 1"      # node graph, versions, group
dvr color cdl --slope 1.0 0.98 0.95 --saturation 55 --where "name contains 'SH010'"
dvr color lut set /path/to/show.cube --node 2
dvr color lut export ./grade.cube --size 65
dvr color version add "client_note_01"
dvr color copy --source PLATE_v003                # grade the rest from one clip
```

Every mutating color command takes `--dry-run`, which prints the clips it
would touch and writes nothing.

## LUT and DCTL files

Resolve loads LUTs and DCTLs from a directory on disk, not from the project,
and the scripting API cannot create or inspect them. These commands manage
that directory; run `dvr render refresh-luts` afterwards so Resolve picks up
new files.

```bash
dvr lut root                                      # where dvr reads and writes
dvr lut ls
dvr lut generate dvr/warm.cube --transform "(r ** 0.8, g ** 0.85, b)" --size 33
dvr dctl write dvr/cool.dctl --file ./cool.dctl
dvr dctl validate ./cool.dctl                     # check without writing
```

`--transform` is a Python expression over `r`, `g` and `b` in `[0, 1]`
returning an `(r, g, b)` tuple. It runs with imports and dunder access
blocked, so it cannot reach the filesystem or network.

DCTL validation is a structural check — entry point and bracket balance — not
a compile. `dvr` runs outside Resolve and has no access to its GPU toolchain,
so errors inside the function body still surface in Resolve's console.

Paths are resolved inside the LUT directory and traversal outside it is
refused. `DVR_LUT_DIR` overrides the location, which matters on Linux and in
tests.
