# Python library

The library is the source of truth — the CLI, daemon, and MCP server are all thin shells over it.

## Connecting

```python
from dvr import Resolve

r = Resolve()                                 # auto-launches Resolve if needed
r = Resolve(auto_launch=False)                # raise if Resolve isn't running
r = Resolve(timeout=60)                       # wait up to 60s for the connection
```

The connection handles macOS's LAN-IP quirk and timeouts every underlying API call so a hung Resolve can't deadlock you.

## Domain accessors

```python
r.app           # page, version, product, quit
r.project       # list, current, create, load, ensure, delete, archive, export, import
r.timeline      # list, current, get, ensure, create, switch, delete (project-scoped)
r.render        # queue, presets, formats, codecs, submit, watch, status, stop
r.storage       # filesystem-side: volumes, file lists, bulk import
```

Within a project:

```python
project = r.project.ensure("MyShow")
project.timeline           # same as r.timeline once project is current
project.media              # MediaPool
project.gallery            # stills, PowerGrades
project.set_setting(key, value)
```

Within a timeline:

```python
tl = r.timeline.current
tl.tracks("video")         # all video tracks
tl.track("video", 2)       # V2
tl.clips("video")          # ClipQuery over all video clips
tl.clips("video").where(lambda c: c.duration > 48)
tl.markers()               # {frame: {...}}
tl.add_marker(120, color="Red", name="check sync")
```

Within a clip:

```python
clip = tl.clips("video").first()
clip.inspect()                                 # full state
clip.set_property("Pan", 0.25)                 # transform/composite/retime
clip.color.set_cdl(slope=(1, 1, 1, 0.95))      # color page operations
clip.color.export_lut("/Volumes/luts/grade.cube", size=33)
clip.fusion.add()                              # add a Fusion comp
clip.takes.add(asset)                          # alternate takes
clip.replace("/Volumes/new_source.mov")        # relink, preserves grades
```

## Idempotent context managers

```python
with r.project.use("MyShow") as project:
    with project.timeline.use("Edit_v2") as tl:
        # ...
        pass
# previous project + timeline restored on exit

# Scoped project setting flips — restored on exit, even on exception.
with project.setting_context("colorAcesODT", "Rec.709 BT.1886"):
    r.render.submit_and_wait(...)
# previous colorAcesODT value restored
```

## Querying

```python
# Find clips matching a predicate.
short_clips = tl.clips("video").where(lambda c: c.duration < 24)
print(len(short_clips))
for clip in short_clips:
    clip.add_marker(color="Red", name="too short")

# Compose queries.
v2_long = tl.clips("video").where(lambda c: c.track_index == 2 and c.duration > 48)
```

## Renders

```python
job = r.render.submit(
    target_dir="/Volumes/out",
    custom_name="MyShow_v2",
    format="mov",
    codec="ProRes4444XQ",
)
job.wait()                                     # block with stall detection
print(job.output_path)

# Or stream events:
for event in r.render.watch([job.id]):
    print(event)

# One-shot: submit, block, return the rendered path.
output = r.render.submit_and_wait(
    target_dir="/Volumes/out",
    custom_name="MyShow_v2",
    format="mov",
    codec="ProRes4444XQ",
)

# Normalized status snapshot — same payload as RenderJob.poll().
snap = r.render.status(job.id)
print(snap["status"], snap["percent"], snap["error"])

# Safe between shots — bounded queue cleanup with timeout, even after
# image-sequence (EXR / DPX) jobs leave the queue stuck at 100%.
r.render.clear()
```

## Media imports

```python
# Idempotent: returns the existing pool clip if the path is already
# imported, otherwise imports it. Useful when many shots come from one
# master and you don't want a duplicate Media Pool entry per shot.
clip = project.media.find_or_import("/Volumes/raw/master_v003.mov")

# IMF (Interoperable Master Format) — pass the OV folder, not the CPL.
clips = project.media.import_imf("/Volumes/deliveries/IMF_OV/")
```

## Interchange

```python
from dvr import interchange

interchange.export(tl, "out.fcpxml", format="fcpxml-1.10")
interchange.export(tl, "out.edl",    format="edl-cdl")
interchange.export(tl, "out.aaf",    format="aaf")
print(interchange.export_formats())  # all 21 supported names

new_tl = interchange.import_(project.media, "incoming.aaf")
```

## Audio and Gallery

```python
from dvr import audio, gallery

audio.set_voice_isolation(tl, enabled=True, amount=70)
audio.apply_fairlight_preset(project, "Dialogue Smooth")

g = gallery.gallery_for(project)
album = g.create_still_album("Hero shots")
album.import_stills(["/Volumes/stills/01.png"])
```

## Errors

Every failure raises a subclass of `dvr.errors.DvrError`. See [Errors and diagnostics](concepts/errors.md).

## Type hints

The library ships with `py.typed` and is checked in mypy `--strict` mode. Domain wrappers that wrap the inherently-`Any` Resolve handles relax `warn_return_any` for ergonomics; everywhere else, types are precise.
