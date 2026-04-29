# Cookbook

Recipes for the workflows that come up most often. Each example assumes
you've already run `dvr ping` successfully, so we know the connection is
working.

## Project setup

### Bootstrap an HDR project from scratch

```yaml
# project.dvr.yaml
project: MyShow
color_preset: rec2020_pq_4000
timelines:
  - name: Edit_v1
    fps: 24
    markers:
      - {frame: 0, color: Blue, name: HEAD}
```

```bash
dvr apply project.dvr.yaml
```

The project, timeline, color science, and head marker are reconciled in
one command. Re-running is a no-op if the live state already matches.

### Bootstrap an ACES project

```yaml
# project.dvr.yaml
project: MyShow
color_preset: aces_rec2020_pq_1000
timelines:
  - name: Edit_v1
    fps: 24
```

```bash
dvr apply project.dvr.yaml
```

The preset sets color science to `acescct` with the AP1 working space.
ACES IDT/ODT must be picked in Resolve's UI (the API silently ignores
HDR PQ ODT names); save a render preset once you've configured them, then
recall it from scripts with `project.set_preset(name)`.

### Switch to an existing project for one operation

```python
from dvr import Resolve

r = Resolve()
with r.project.use("MyShow"):
    print(r.timeline.current.inspect())
# previous project is restored on exit
```

### Save a checkpoint before doing something risky

```bash
dvr snapshot save before-grade-experiment
# ... try things ...
dvr snapshot restore before-grade-experiment
```

---

## Timeline operations

### Find every clip shorter than 12 frames and flag it

```bash
dvr clip mark --where "duration < 12" --color Red --name "too short"
```

### Set composite mode + opacity on every clip on V2

```bash
dvr clip set --where "track_index == 2" CompositeMode=Difference Opacity=70
```

### Diff this timeline against last week's

```bash
dvr snapshot save last-week                 # last week
# ... time passes, edits happen ...
dvr diff snapshot last-week
```

### Compare two timelines side by side

```bash
dvr diff timelines Edit_v1 Edit_v2 --format yaml | less
```

---

## Rendering

### Render with pre-flight checks

```bash
dvr render submit --target-dir /Volumes/out --preflight --wait
```

`--preflight` runs `dvr lint` first and aborts if any errors are found —
no offline media surprise mid-render.

### Render a specific shot range, watch progress live

```bash
dvr render submit \
  --target-dir /Volumes/out \
  --preset Delivery \
  --wait
# Rich progress bar in the terminal; switches to JSON when piped.
```

### Submit, get back the job ID, then poll later

```bash
JOB=$(dvr render submit --target-dir /Volumes/out --no-wait | jq -r .job_id)
dvr render status "$JOB"
```

### Submit, block, get the rendered path back

```python
from dvr import Resolve

r = Resolve()
output = r.render.submit_and_wait(
    target_dir="/Volumes/out",
    custom_name="delivery_master",
    format="mov",
    codec="ProRes4444XQ",
)
print(output)  # /Volumes/out/delivery_master.mov
```

One call covers `submit(start=True)`, `wait()`, and the post-completion
path lookup, with a clear error if Resolve evicts the job from the queue
before the path can be read.

### Stream render events to a webhook (e.g., Slack)

```bash
dvr render submit --target-dir /Volumes/out --stream | while read event; do
  status=$(echo "$event" | jq -r .type)
  if [ "$status" = "complete" ]; then
    output=$(echo "$event" | jq -r .output_path)
    curl -X POST -d "{\"text\":\"render done: $output\"}" "$SLACK_HOOK"
  fi
done
```

---

## Media

### Bulk-import a folder of plates

```bash
dvr media mkbin Plates
dvr media import --bin Plates /Volumes/work/plates/*.mov
```

### Import a file once, even if the script runs many times

```python
from dvr import Resolve

r = Resolve()
clip = r.project.current.media.find_or_import("/Volumes/raw/master.mov")
```

`find_or_import` walks the pool for a clip whose path matches and only
imports if it's missing. Useful when many shots come out of the same
master and you don't want a duplicate Media Pool entry per call.

### Import an IMF (Interoperable Master Format) package

```python
from dvr import Resolve

r = Resolve()
clips = r.project.current.media.import_imf("/Volumes/deliveries/IMF_OV/")
```

Pass the IMF *folder* (the one containing `ASSETMAP.xml`, `CPL_*.xml`,
`PKL_*.xml`, and the `.mxf` essence files) — not the CPL XML. Each MXF
becomes its own pool clip; the XML manifests are recognized and skipped.

### Relink an entire bin to new locations

```bash
dvr media relink /Volumes/sync/new_plates --bin Plates
```

### Find every offline asset

```bash
dvr media ls --format json | jq '.[] | select(.file_path == "")'
```

---

## Color

### Apply the same CDL to every clip on V2

```python
from dvr import Resolve

r = Resolve()
slope = (1.05, 1.05, 1.05, 1.0)
for clip in r.timeline.current.clips("video").where(lambda c: c.track_index == 2):
    clip.color.set_cdl(node_index=1, slope=slope)
```

### Export a 33-point cube LUT from the current grade

```python
clip.color.export_lut("/Volumes/luts/master_grade.cube", size=33)
```

### Stabilize every shot in a single command

```bash
dvr clip set --where "track_index == 2" --dry-run     # preview targets
dvr eval "[c.color.stabilize() for c in r.timeline.current.clips('video').where(lambda c: c.track_index == 2)]"
```

---

## Subtitles

### Auto-transcribe a timeline with Whisper

```python
from dvr import Resolve

r = Resolve()
r.timeline.current.create_subtitles_from_audio(language="en", chars_per_line=42)
```

The audio track has to exist on the timeline. Resolve will pop a
progress bar in its UI; the call is fire-and-forget.

---

## Interchange

### Export every interchange format we support

```bash
for fmt in fcpxml-1.10 edl drt otio aaf; do
  dvr eval "import dvr.interchange as ix; print(ix.export(r.timeline.current, '/Volumes/out/round1.$fmt', format='$fmt'))"
done
```

---

## Scripting

### Run a one-line query

```bash
dvr eval "[t.name for t in r.timeline.list()]"
dvr eval "r.timeline.current.duration_frames / r.timeline.current.fps"
```

### Run a Python file with `r` pre-bound

```python
# script.py
for tl in r.timeline.list():
    print(tl.name, tl.duration_frames, tl.fps)
```

```bash
dvr exec script.py
```

### Drop into a REPL

```bash
dvr repl
# >>> r.app.version
# '20.3.1'
# >>> r.timeline.current.inspect()
```

---

## Daemon mode

### Speed up sequential commands

```bash
dvr serve start
dvr timeline list                 # ~50ms
dvr timeline inspect              # ~50ms
dvr timeline switch Edit_v2       # ~50ms
dvr serve stop
```

After `serve start`, every `dvr` invocation reuses the same Resolve
connection. The daemon also auto-reconnects if Resolve restarts.

---

## LLM / agent integration

### Wire `dvr` into Claude Desktop

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

Then ask Claude to inspect, diff, lint, snapshot, or render — it calls
typed tools instead of parsing shell output.
