# Inspection

The Resolve API forces you to navigate by chained getters: `project → timeline → track → item → property → marker`. Reading the full state of even a small timeline can take 50+ API calls, each of which can return `None`.

`dvr` provides a single canonical read for every domain:

```python
r.inspect()                      # Resolve + current project + current timeline
r.app.inspect()                  # version, page, product
r.project.current.inspect()      # name, timelines, current timeline
r.timeline.current.inspect()     # full timeline structure
clip.inspect()                   # full clip state, grades, Fusion comps
r.render.queue()                 # all render jobs as plain dicts
asset.inspect()                  # full media-pool item snapshot
```

Each `inspect()` returns a JSON-friendly dictionary. The structure is stable across releases (additions only).

## Why this matters

For LLM agents, one structured read is cheaper than many small ones — both in round-trips and token cost. `Timeline.inspect()` returns:

```json
{
  "name": "MyShow_207_R2",
  "fps": 24.0,
  "duration_frames": 86400,
  "start_timecode": "01:00:00:00",
  "tracks": {
    "video": [
      {"type": "video", "index": 1, "name": "V1", "enabled": true, "clip_count": 1},
      {"type": "video", "index": 2, "name": "V2", "enabled": true, "clip_count": 21}
    ],
    "audio": [
      {"type": "audio", "index": 1, "name": "A1", "enabled": true, "clip_count": 4, "subtype": "stereo"}
    ]
  },
  "marker_count": 12
}
```

You can drive logic off the snapshot without further API calls until you need to mutate something.

## Streaming

Renders are the one read-loop where snapshots aren't enough. Use the streaming API:

```python
for event in r.render.watch():
    print(event)
```

```json
{"type": "progress", "job_id": "abc", "status": "Rendering", "percent": 12, "eta_s": 240}
{"type": "complete", "job_id": "abc", "output_path": "/Volumes/out.mov", "time_s": 380}
```

The CLI exposes the same as `dvr render watch`, which writes newline-delimited JSON.
