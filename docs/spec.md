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

## Schema

```yaml
project: <name>                  # required — project to ensure exists
color_preset: <preset-name>      # optional — see below for valid presets
settings:                        # optional — raw project setting overrides
  <key>: <value>
timelines:                       # optional — timelines to ensure
  - name: <name>                 # required
    fps: <number>                # optional
    settings:                    # optional — timeline setting overrides
      <key>: <value>
    markers:                     # optional
      - frame: <int>
        color: <Blue|Red|Green|...>
        name: <string>
        note: <string>
        duration: <int>
        custom_data: <string>
```

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
