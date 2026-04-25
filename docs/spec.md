# Declarative specs

`dvr apply` reconciles the live state of DaVinci Resolve against a declared spec, the way `kubectl apply` works for Kubernetes. You describe what you want; `dvr` computes the diff and applies only the deltas.

## Quickstart

Save this as `myshow.dvr.yaml`:

```yaml
project: MyShow_207
color_preset: rec2020_pq_4000

timelines:
  - name: ROUND_2
    fps: 24
    markers:
      - {frame: 0,    color: Blue, name: HEAD}
      - {frame: 1440, color: Red,  name: 60s}
```

Preview:

```bash
dvr plan myshow.dvr.yaml
```

Apply:

```bash
dvr apply myshow.dvr.yaml
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

Built-in presets apply a complete HDR color-management config in the order Resolve requires (color science mode first, then color spaces, then luminance / mastering settings):

| Preset | Color space | Gamma | Luminance |
|--------|------------|-------|-----------|
| `rec2020_pq_4000` | Rec.2020 | ST2084 (PQ) | HDR 4000 nits |
| `p3d65_pq_1000` | P3-D65 | ST2084 (PQ) | HDR 1000 nits |
| `rec709_gamma24` | Rec.709 | Gamma 2.4 | SDR |

Use `settings:` to override individual keys on top of (or instead of) a preset.

## Plan output

`dvr plan` (and `dvr apply` before applying) emits a structured action list:

```json
[
  {"op": "noop",   "target": "project:MyShow_207", "detail": "already exists"},
  {"op": "set",    "target": "project:MyShow_207/setting:colorScienceMode", "detail": "= davinciYRGBColorManagedv2"},
  {"op": "set",    "target": "project:MyShow_207/setting:colorSpaceTimeline", "detail": "= Rec.2020"},
  {"op": "ensure", "target": "timeline:ROUND_2", "detail": "in project MyShow_207"},
  {"op": "set",    "target": "timeline:ROUND_2/setting:timelineFrameRate", "detail": "= 24"},
  {"op": "set",    "target": "timeline:ROUND_2/marker:0", "detail": "HEAD"}
]
```

## Programmatic use

```python
from dvr import Resolve, spec

r = Resolve()
parsed = spec.load_spec("myshow.dvr.yaml")
plan = spec.plan(parsed, r)         # list[Action]
spec.apply(parsed, r, dry_run=True) # same as plan
spec.apply(parsed, r)               # actually applies
```
