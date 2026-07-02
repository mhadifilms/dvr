# Declarative specs

`dvr apply` reconciles the live state of DaVinci Resolve against a declared spec, the way `kubectl apply` works for Kubernetes. You describe what you want; `dvr` computes the diff and applies only the deltas.

## Quickstart

Save this as `project.dvr.yaml`:

```yaml
project: MyShow
color_preset: rec2020_pq_4000

timelines:
  - name: Edit_v2
    fps: 24
    markers:
      - {frame: 0,    color: Blue, name: HEAD}
      - {frame: 1440, color: Red,  name: 60s}
```

Preview:

```bash
dvr plan project.dvr.yaml
```

Apply:

```bash
dvr apply project.dvr.yaml
```

Re-running is a no-op if the live state already matches.

## Safety levers

```bash
dvr apply show.yaml --transactional   # snapshot first; auto-rollback on failure
dvr apply show.yaml --verify          # read every setting back after writing
```

`--transactional` captures a snapshot of the project before mutating anything. If any action fails, the snapshot is restored and the error reports the rollback (and the snapshot name, in case you want it later). When the project doesn't exist yet there is nothing to roll back to, and the original error propagates unchanged.

`--verify` catches Resolve's most maddening failure mode — `SetSetting` returning success while silently ignoring the value. Every write is read back; a mismatch raises a `SettingsError` with what was written and what stuck.

Both are exposed on the `apply_spec` MCP tool (`transactional` / `verify`) and on `dvr.spec.apply(...)`.

## Adopting an existing project

```bash
dvr spec export -o show.yaml          # current project
dvr spec export MyShow -o show.yaml   # by name
```

`spec export` (library: `dvr.spec.from_live()`) reverse-engineers a spec from live state — the settings subset dvr round-trips, the bin tree, and each timeline's fps, track counts, and markers. From then on the project can be managed declaratively with `dvr plan` / `dvr apply`.

## Schema

```yaml
project: <name>                  # required — project to ensure exists
color_preset: <preset-name>      # optional — see below for valid presets
settings:                        # optional — raw project setting overrides
  <key>: <value>
bins:                            # optional — media-pool bins to ensure
  - Footage/Day01                # nested "A/B/C" paths, created idempotently
  - Audio
timelines:                       # optional — timelines to ensure
  - name: <name>                 # required
    fps: <number>                # optional
    tracks:                      # optional — minimum track counts
      video: 3                   # tracks are added until the count is met;
      audio: 4                   # existing extra tracks are never removed
    settings:                    # optional — timeline setting overrides
      <key>: <value>
    markers:                     # optional
      - frame: <int>
        color: <Blue|Red|Green|...>
        name: <string>
        note: <string>
        duration: <int>
        custom_data: <string>
    clip_properties:             # optional — static TimelineItem.SetProperty controls
      - selector:
          track_type: video
          track_index: 2
          name_contains: "plate"
        properties:
          crop_top: 120
          crop_bottom: 120
          blend: multiply
          opacity: 80
    titles:                      # optional — on-screen Text+ titles
      - text: "OPENING TITLE"    # required — also the idempotency key
        at: "01:00:02:00"        # optional — timecode to place a new title
        title: "Text+"           # optional — defaults to the Text+ generator
        font: "Open Sans"
        size: 0.12
        color: "#ffcc00"         # hex, name, or [r, g, b]
        align: center            # left | center | right
        vertical_align: center   # top | center | bottom
```

`clip_properties` entries select timeline items, normalize friendly keys
through `dvr.schema`, and apply only values that differ from the current
`GetProperty` value. Selectors support `track_type`, `track_index`, `name`,
`name_contains`, `start`, `end`, `duration_lt`, and `duration_gt`.

`titles` entries are matched by their `text`: if a video item with a Text+ tool
already shows that string, its styling is updated in place; otherwise a new
title is inserted (seeking to `at` first when given). That keeps re-runs
idempotent. Styling accepts `font`, `style`, `size`, `color`, `opacity`,
`tracking`, `line_spacing`, `position`, `align`, and `vertical_align`.

Supported properties are Resolve's documented static timeline-item controls:
transform, crop, dynamic zoom ease, composite, retime quality, scaling, and
resize filters. General keyframe animation and edit-page transitions are
not exposed by Resolve's scripting API.

## Color presets

Built-in presets apply a complete color-management config in the order
Resolve requires (color science mode first, then color spaces, then
luminance / mastering settings):

### DaVinci Color Managed v2

| Preset | Color space | Gamma | Luminance |
|--------|-------------|-------|-----------|
| `rec2020_pq_4000` | Rec.2020 | ST2084 (PQ) | HDR 4000 nits |
| `p3d65_pq_1000`   | P3-D65   | ST2084 (PQ) | HDR 1000 nits |
| `rec709_gamma24`  | Rec.709  | Gamma 2.4   | SDR |

### ACES (ACEScct)

| Preset | Display | Luminance |
|--------|---------|-----------|
| `aces_p3d65_pq_4000`   | P3-D65   | HDR 4000 nits |
| `aces_p3d65_pq_1000`   | P3-D65   | HDR 1000 nits |
| `aces_rec2020_pq_4000` | Rec.2020 | HDR 4000 nits |
| `aces_rec2020_pq_1000` | Rec.2020 | HDR 1000 nits |
| `aces_rec709`          | Rec.709  | SDR |

ACES presets set color science to `acescct` with the AP1 working space.
The HDR variants also bump `timelineWorkingLuminanceMode` and
`hdrMasteringLuminanceMax` so Resolve's HDR UI sizes correctly.

!!! note "ACES IDT/ODT must be picked in the Resolve UI"
    Resolve's API silently rejects HDR PQ ODT names (every documented
    spelling — UI labels, ACES 1.x AMF names, ACES 2.0 names, internal
    binary names). The reliable workflow is to pick the IDT/ODT once in
    the UI, save a render preset, and recall it from scripts:

    ```python
    project.set_preset("MyShow_ACES_HDR")  # set up once in the UI
    ```

    `Project.set_aces_idt(value)` and `Project.set_aces_odt(value)` work
    for the values Resolve does accept and raise a `SettingsError`
    pointing at this workaround when it doesn't.

Use `settings:` to override individual keys on top of (or instead of) a
preset.

## Plan output

`dvr plan` (and `dvr apply` before applying) emits a structured action list:

```json
[
  {"op": "noop",   "target": "project:MyShow", "detail": "already exists"},
  {"op": "set",    "target": "project:MyShow/setting:colorScienceMode", "detail": "= davinciYRGBColorManagedv2"},
  {"op": "set",    "target": "project:MyShow/setting:colorSpaceTimeline", "detail": "= Rec.2020"},
  {"op": "ensure", "target": "timeline:Edit_v2", "detail": "in project MyShow"},
  {"op": "set",    "target": "timeline:Edit_v2/setting:timelineFrameRate", "detail": "= 24"},
  {"op": "set",    "target": "timeline:Edit_v2/marker:0", "detail": "HEAD"}
]
```

## Programmatic use

```python
from dvr import Resolve, spec

r = Resolve()
parsed = spec.load_spec("project.dvr.yaml")
plan = spec.plan(parsed, r)         # list[Action]
spec.apply(parsed, r, dry_run=True) # same as plan
spec.apply(parsed, r)               # actually applies
```
