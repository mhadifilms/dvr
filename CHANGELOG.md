# Changelog

All notable changes to `dvr` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-04-28

Additive release driven by build-pipeline ergonomics and ACES support.
No breaking changes — every new method has a backwards-compatible
default. The IMF and ACES additions are the headline; the two
single-call helpers (``find_or_import``, ``submit_and_wait``) drop the
boilerplate that every long-running build script ends up reinventing.

### Added — ACES color management

- `dvr.spec.COLOR_PRESETS` — five new ACES presets:
  `aces_p3d65_pq_4000`, `aces_p3d65_pq_1000`, `aces_rec2020_pq_4000`,
  `aces_rec2020_pq_1000`, `aces_rec709`. Each sets color science to
  `acescct` with AP1 working space; HDR variants also bump
  `timelineWorkingLuminanceMode` and `hdrMasteringLuminanceMax` so the
  HDR UI sizes correctly. ACES IDT/ODT must still be picked in the
  Resolve UI — see below.
- `dvr.spec.SETTINGS_ORDER` — appended ACES keys
  (`colorAcesNodeLUTProcessingSpace`, `colorAcesGamutCompressType`,
  `colorAcesIDT`, `colorAcesODT`) so `spec.apply` writes them after
  `colorScienceMode` flips on.
- `Project.set_aces_idt(value)` and `Project.set_aces_odt(value)` —
  thin wrappers over `set_setting("colorAcesIDT", ...)` /
  `set_setting("colorAcesODT", ...)` with a clearer error path.
- `Project.set_setting` — now special-cases the well-known HDR PQ
  IDT/ODT rejection. When Resolve refuses an HDR PQ value (every
  documented format — UI labels, ACES 1.x AMF names, ACES 2.0 AMF
  names, internal binary names — is silently dropped by the API),
  raise a `SettingsError` that points at the working UI / preset
  workaround instead of the generic "wrong type" hint.
- `Project.presets()`, `Project.set_preset(name)`,
  `Project.save_as_preset(name)` — wrappers around `GetPresetList`,
  `SetPreset`, and `SaveAsNewRenderPreset`. The intended workflow for
  HDR PQ ACES projects: save a preset once in the Resolve UI with the
  desired IDT/ODT, then call `project.set_preset(name)` from scripts.

### Added — IMF (Interoperable Master Format) ingest

- `MediaPool.import_imf(imf_dir, *, folder=None)` — imports an IMF OV
  package into the pool. Pass the IMF *folder* (containing
  `ASSETMAP.xml`, `CPL_*.xml`, `PKL_*.xml`, and the `.mxf` essence
  files), not the CPL XML itself. Resolve's
  `MediaPool.ImportMedia([cpl_path])` returns empty for IMFs;
  `MediaStorage.AddItemListToMediaPool([imf_dir])` is the working path
  and is what this method drives. Each MXF is imported as a separate
  pool clip; CPL/PKL/ASSETMAP/OPL XMLs are recognized and skipped by
  Resolve.
- `import_imf` validates that the path is a directory and that at
  least one `CPL_*.xml` exists before calling Resolve, so a typo
  surfaces as a `MediaImportError` with `fix=` guidance instead of an
  opaque empty-list return.

### Added — build-script ergonomics

- `MediaPool.find_or_import(path, *, folder=None) -> Clip` — the
  primitive that batch scripts kept reinventing. Walks the pool for a
  clip whose `file_path` matches the requested path (after
  `os.path.normpath` / `os.path.normcase`); imports via `import_media`
  / `import_to` only if absent. Without it, every call to
  `import_media` adds a duplicate Media Pool entry for the same path,
  which slows projects that cut many shots out of one master.
- `RenderNamespace.submit_and_wait(*, target_dir, custom_name=..., ...)`
  — submit + wait one-shot. Returns the absolute output path
  (`OutputFilename`). Equivalent to `r.render.submit(...).wait()` plus
  the post-completion path lookup, with a clear error if Resolve
  evicts the job from the queue before the path can be read.

### Improved

- `ProjectNamespace.load(name)` — when `LoadProject` returns `None`,
  distinguish "project doesn't exist in this PM folder" from "project
  exists but Resolve refused to swap to it" (typically because another
  project has unsaved changes). The "refused to swap" error now names
  the currently-open project and tells the caller to save/close it
  first.

## [1.0.0] - 2026-04-25

First stable release. The 0.x line was driven by integration feedback;
1.0.0 graduates the API after end-to-end production validation against
real Resolve Studio 20.3 build + render workflows.

The public surface is stable from here. Breaking changes ship with a
deprecation cycle and a major version bump. New features land as
minors; bug fixes as patches.

### Stable surface

- `Resolve` — connection, app/page control, project / timeline / render
  / storage namespaces, context manager.
- `Project` — settings, save/close, media pool accessor, timeline
  namespace, gallery, typed `settings` proxy.
- `ProjectNamespace` — current, list, ensure, load, create, archive,
  import/export, `use(...)` context manager.
- `Timeline` — tracks, items, markers, settings, `duplicate`,
  `find_clip`, `find_clips`, `find_gaps`, `create_compound_from_clips`,
  `delete_clips`, `create_subtitles_from_audio`, `detect_scene_cuts`,
  `inspect`.
- `Track`, `TrackList`, `TrackCollection` — typed accessors with
  `find` / `find_all` / `add` / `delete`.
- `TimelineItem` — properties, marker add, `replace`, `source_range`,
  `is_compound`, `set_property(raise_on_failure=...)`, color / fusion /
  takes accessors, color/fusion cache control.
- `MarkerCollection` — dict-like + `add` / `remove` / `remove_color` /
  `find` / `where`.
- `Clip` (media-pool item) — properties, metadata, flags, color,
  markers, mark in/out, proxy, `replace`, `transcribe`,
  `set_property(raise_on_failure=...)`.
- `Folder` — clips, subfolders, `walk`, `all_clips`, `find_clip(s)`,
  `add_subfolder`, `rename`, `delete`, `move`, `transcribe`, `export`.
- `MediaPool` — root, current folder, ensure / add / find folder,
  `walk`, `find_clip(s)`, `delete_clips`, `delete_folders`,
  `delete_timelines`, `import_media`, `import_to`, `import_timeline`,
  `create_empty_timeline`, `create_timeline_from_clips`,
  `append_to_timeline`, `auto_sync_audio`, `import_with_subclips`,
  `create_subclip`.
- `MediaStorage` — volumes, subfolders, files, reveal, add to pool.
- `RenderJob` — id, status, percent, progress, eta, output_path,
  `cancel`, `wait` (with stall + timeout), `poll` / `inspect`.
- `RenderNamespace` — formats / codecs / presets, queue / clear /
  stop, `submit`, `submit_per_clip`, `render_single_clip`, `watch`,
  `is_rendering`. Tolerates `None` returns from headless Resolve and
  raises a clear error if invoked while a render is already in flight.
- CLI — `dvr inspect | ping | page | project | timeline | clip | media |
  render | diff | snapshot | schema | serve | mcp | apply | lint |
  script | completion | plugin`. Plugin protocol via the
  `dvr.plugins` entry-point group plus a user manifest at
  `~/.config/dvr/plugins.toml`.
- Daemon (`dvr serve`) and MCP server (`dvr mcp`) for long-lived
  process and LLM-tool integrations.
- Errors — typed hierarchy under `dvr.errors`, every error carries
  `cause` / `fix` / `state` for actionable failure modes.

### Validation

End-to-end exercised against Resolve Studio 20.3.2 macOS:

- HDR project setup with full color-management config (P3-D65 / PQ /
  1000 nits), 17 settings applied successfully.
- Stitched timeline build of a 1882-shot Sheet, 497 V2 clips placed,
  letterbox crop, audio stems, full verify report.
- H.265 full-timeline render via the built-in `H.265 Master` preset
  → 13.45 GB MOV in 38 minutes.

## [0.5.1] - 2026-04-25

Hotfix for two real-world failure modes surfaced during a build +
render run on Resolve Studio 20.3.2 macOS.

### Fixed

- `_open_page` — Resolve `OpenPage` returns `None` (not `False`/`True`)
  on headless / render-farm instances and on macOS when the UI is
  obscured. Previously dvr treated that as a fatal `DvrError` and
  refused every page change. Now: if a project is loaded,
  silently treat a `None` return as success — page state is cosmetic
  for almost every API and renders run regardless. Genuine "no project
  loaded" cases still raise.
- `RenderNamespace.submit` and `submit_per_clip` — same Resolve quirk
  hits `SetRenderSettings` and `StartRendering`, which return `None`
  on success in some builds. Previously `if not ok:` raised on every
  call. Now: only raise when the call returns the literal `False`,
  and verify the queue actually changed via the existing
  before/after diff.
- Both submit paths now check `IsRenderingInProgress()` up front. If
  Resolve is busy rendering, every queue mutation is silently ignored,
  which used to surface as the cryptic "SetRenderSettings returned
  False". Now it raises a clear "Resolve is currently rendering;
  wait or stop the in-progress job" error with the queue size in state.

## [0.5.0] - 2026-04-25

Coverage pass — every primitive that integration consumers had to drop
to raw fusionscript for is now wrapped. No deprecations; everything
added is purely additive on top of 0.4.

### Added — Tier 1 (replaces raw API call sites)

- `Timeline.duplicate(name=None)` — wraps `DuplicateTimeline`. Returns
  a `Timeline`, raises `TimelineError` on collision. Drops the last
  reason build pipelines had to keep `source_tl.DuplicateTimeline(...)`
  calls.
- `MediaPool.delete_timelines(timelines)` — accepts a `Timeline`, name
  string, or iterable of either. Resolves names against the project.
- `MediaPool.delete_folders(folders)` — wraps `DeleteFolders` for one
  or many folders.
- `Folder.delete()` and `Folder.rename(name)` (also `folder.name = ...`).
  Folder reorg no longer needs raw access.
- `Folder.walk()` and `Folder.all_clips()` — recursive iterators for
  "every folder/clip beneath here". Replaces ad-hoc `build_clip_lookup`
  helpers callers used to write themselves.
- `MediaPool.walk()`, `MediaPool.find_clip(name= or predicate=)`,
  `MediaPool.find_clips(...)`, `MediaPool.find_folder(name)` — typed
  recursive lookup primitives.
- `Folder.find_clip(...)` / `Folder.find_clips(...)` — same shape, scoped
  to a single folder subtree.
- `Track.find(name= or predicate=)` and `Track.find_all(...)` — first /
  all matches on a track.
- `Timeline.find_clip(name= or predicate=, track_type=...)` and
  `Timeline.find_clips(...)` — search across all (or filtered) tracks.

### Added — Tier 2 (ergonomics)

- `TimelineItem.set_property(key, value, raise_on_failure=True)` and
  `Clip.set_property(key, value, raise_on_failure=True)` now return a
  `bool`. Pass `raise_on_failure=False` for batch counting like
  `sum(1 for c in clips if c.set_property(...))`.
- `MediaPool.import_to(folder, paths, create_missing=True)` — idempotent
  "import these into this folder, restore the previous folder
  selection on exit". Folder may be a `Folder` or its name.
- `MarkerCollection.find(color=..., name=..., custom_data=...)` returns
  `[(frame, marker), ...]` — exact-match query for the common
  "find all red markers" / "find by customData" cases.
- `MarkerCollection.where(predicate)` — predicate-based query taking
  `(frame, marker)` and returning bool.

### Added — Tier 3 (transformative)

- `MediaPool.create_subclip(source_path, *, start, end, name=None,
  folder=None)` — typed sub-clip primitive returning a `Clip`. EDL
  ingestion collapses to a one-liner per entry.
- `RenderNamespace.submit_per_clip(items, *, target_dir,
  naming_template, ...)` — queue one render job per timeline item with
  per-clip MarkIn/MarkOut and templated `CustomName`. Returns a list of
  `RenderJob` you can `watch([j.id for j in jobs])`.
- `RenderNamespace.render_single_clip(item, *, target_dir, ...)` —
  convenience for "render exactly this one timeline item". Returns a
  single `RenderJob`.
- **CLI plugin protocol**. Two discovery mechanisms:
  - **Entry points** (preferred): a package declares
    `[project.entry-points."dvr.plugins"] myshow = "myshow.cli:plugin"`,
    and after `pip install`, `dvr myshow ...` is a first-class
    subcommand.
  - **User manifest** (`~/.config/dvr/plugins.toml`): manage with
    `dvr plugin add <name> <path-or-module>`, `dvr plugin remove`,
    `dvr plugin list`. Suitable for local repos and dev work.
  - A plugin's exported value can be a `typer.Typer` instance or a
    `register(app)` callable.

### Added — Tier 4 (speculative)

- `Timeline.find_gaps(track_type="video", track_index=1)` — returns
  `[(start_frame, end_frame), ...]` for every gap on a track,
  including leading and trailing space.
- `Project.settings` typed proxy. Read with attribute access
  (`proj.settings.timeline_resolution_width`), write the same way.
  Maps snake_case attribute names to Resolve's camelCase keys for
  common settings (timeline resolution, frame rate, color science,
  tone mapping, etc.). Falls through to string keys for anything not
  pre-mapped, so unknown settings still work.

### Notes

- `RenderNamespace.submit(preset=, format=, codec=)` already supported
  the combined "load preset, then override" pattern in 0.4 and is
  unchanged here.
- The new `submit_per_clip` writes `SelectAllFrames=False` and explicit
  MarkIn/MarkOut per job. If your existing `submit` workflow depends
  on `SelectAllFrames=True`, that path is unchanged.
- Color-page single-clip rendering: in Resolve's API, "render one
  clip" maps to "set the timeline marks around the item, then
  render". `render_single_clip` does exactly that and is the
  recommended primitive — Resolve does not expose a separate
  color-page render entry point that bypasses timeline marks.

## [0.4.0] - 2026-04-25

API ergonomics pass driven by real-world integration feedback. Every
rename ships with a back-compat alias — existing 0.3.x code continues
to import.

### Renamed (with back-compat aliases)

- `dvr.media.Asset` → `dvr.media.Clip`. The class on a *bin* is now
  called `Clip`. `Asset` and `MediaPoolItem` remain as deprecated
  aliases.
- `dvr.media.Bin` → `dvr.media.Folder`. `Bin` remains as an alias.
- `dvr.timeline.Clip` → `dvr.timeline.TimelineItem`. The thing on a
  *track* is now `TimelineItem`. Within `dvr.timeline`, `Clip` is kept
  as a deprecated alias so `from dvr.timeline import Clip` still works.
  **Note:** the package-level `dvr.Clip` now refers to
  `dvr.media.Clip` (the media-pool item), not the timeline item.
- `dvr.timeline.ClipQuery` → `ItemQuery` (alias kept).
- `dvr.timeline.ClipFusion` → `ItemFusion` (alias kept).

### Added — namespaces and shortcuts

- `tl.tracks` is now a `TrackCollection`. Use `tl.tracks.video[0]` for
  V1, `tl.tracks.audio.add()` to append, `for tr in tl.tracks` to
  iterate every track type. The legacy callable form
  `tl.tracks("video")` still returns a list.
- `track.items` is the canonical accessor for timeline items on a
  track (was `track.clips()`; the method form is kept as a legacy
  alias).
- `tl.markers` is now a `MarkerCollection` — dict-like access by
  frame, with `tl.markers.add(120, color="Red")` and
  `tl.markers.remove(120)`. Legacy callable form `tl.markers()` still
  returns a plain dict.
- `Folder.clips` and `Folder.subfolders` are now properties.
  `Folder.add_subfolder(name)` and `Folder.move(clips, into=...)` live
  on the Folder itself.
- `MediaPool.root` is now a property. `MediaPool.delete_clips`,
  `MediaPool.import_media`, and `MediaPool.create_timeline_from_clips`
  are the canonical names; the older method names remain as aliases.
- `MediaPool.import_with_subclips(items, folder=...)` for EDL-driven
  sub-clip ingestion.
- `Project.timelines` is a plural alias for `Project.timeline` that
  reads better in `for tl in project.timelines:` loops.
- `Project.current_timeline` is now settable: assigning a `Timeline`
  or its name passes through to `timeline.set_current(...)`.
- `Resolve.page` is a shortcut for `Resolve.app.page`. Returns a
  string-like `PageController` with `r.page == "edit"` semantics and a
  context manager: `with r.page.use("color"): ...`.
- `Resolve.project_manager` and `Resolve.pm` expose the raw Resolve
  ProjectManager handle without going through `r.raw`.
- `Resolve` is now a context manager. `with Resolve() as r: ...`
  cancels any in-progress render on exit.
- `TimelineItem.clip` is the canonical accessor for the underlying
  media-pool clip (was `.asset`; alias kept).
- `TimelineItem.create_magic_mask(mode)` shortcut.
- New `Clip` properties: `path`, `codec`, `audio_codec`, `kind`.

### Added — render

- `RenderJob.progress` returns a `[0.0, 1.0]` float (alongside
  `.percent`).
- `RenderJob.is_finished` / `is_complete` / `is_failed` predicates.
- `RenderJob.poll()` is the non-blocking status snapshot — the same
  payload as `.inspect()`, designed for polling loops and dashboards.
- `RenderJob.wait()` now raises `RenderJobError` (a subclass of
  `RenderError`) on Failed/Cancelled, so callers can `except
  RenderJobError` without string-matching.

### Added — errors

- New error subclasses, all inheriting from existing types so
  `except` clauses against the parents continue to catch them:
  - `MediaImportError` (subclass of `MediaError`)
  - `TimelineNotFoundError` (subclass of `TimelineError`) — raised by
    `TimelineNamespace.get` on misses.
  - `RenderJobError` (subclass of `RenderError`)

### Notes

- No changes to the CLI command surface or JSON output schemas.
- This is the **integration release**. The 1.0 stability promotion
  follows real-world validation against 0.4.x.

## [0.3.0] - 2026-04-25

### Added — new primitives
- `Track.delete()` and `Timeline.delete_track(type, index)` — wraps Resolve's `DeleteTrack`. Clean alternative to looping plus track-removal hacks.
- `Timeline.delete_clips(clips, ripple=False)` — batch delete of timeline items, with optional ripple.
- `Timeline.create_compound_from_clips(clips, *, name, start_timecode=None)` — wraps `CreateCompoundClip`. Returns the new compound as a `Clip` ready for further use.
- `Clip.is_compound` — predicate replacing the ad-hoc "no MediaPoolItem and Type == Compound Clip" heuristic that downstream tools were maintaining.
- `Clip.source_range` — `(source_start_frame, source_end_frame)` tuple from `GetSourceStartFrame/EndFrame`.

### Improved
- `RenderNamespace.set_format_and_codec` now reads back the current pair after assignment and raises a structured `RenderError` if Resolve silently rejected the request (the underlying `SetCurrentRenderFormatAndCodec` returns `None` either way).
- `RenderNamespace` gains preset lifecycle methods: `save_preset(name)`, `delete_preset(name)`, `export_preset(name, file_path)`, `import_preset(file_path)`.
- `dvr render submit` gains `--preflight` (runs `dvr lint` first, aborts on errors) and an automatic Rich progress bar when stdout is a TTY.
- Daemon (`dvr serve`) auto-reconnects: each request fetches a live `Resolve` handle, drops the cache on stale-connection errors, and the eager startup connect now warns instead of fatally erroring so Resolve can be launched after the daemon.
- MCP server expanded with `diff_timelines`, `diff_to_spec`, `snapshot_save/list/restore`, `lint`, `schema`, `eval`, and `page_get` tools — full parity with the CLI surface.

### Tests
- New shared `tests/conftest.py` exposes a `mock_resolve` fixture: a wired tree of MockNodes (Resolve → ProjectManager → Project → Timeline → MediaPool) for unit-testing wrappers without a live Resolve install.
- New `tests/test_wrappers_with_mock.py` exercises the wrapper modules end-to-end with the mock fixture.
- New `tests/test_timeline_primitives.py` covers each of the five new primitives plus regression tests for one-shot iterators on `create_compound_from_clips`.

### Build / CI
- CI matrix expands to include `windows-latest` alongside `macos-latest` and `ubuntu-latest`.
- README gains PyPI / Python / CI / docs / license badges.

## [0.2.0] - 2026-04-25

### Added — capabilities that go beyond the raw Resolve API
- `dvr diff`: structured comparison between two timelines, between a snapshot and live state, or between a spec and live state. Resolve has no built-in compare; this is the first one. Lists align by `name`/`id`/`shot_id`/`frame`/`index` to avoid spurious "everything changed" noise.
- `dvr snapshot`: save / list / show / restore / delete project snapshots to disk. Captures color settings, every timeline, every marker. Survives across sessions in a way Resolve's per-action undo stack does not.
- `dvr lint`: pre-flight validation with structured `error` / `warning` / `info` severities. Default rules check: project loaded, timeline loaded, FPS consistency, empty timelines, render format/codec set, color science set. Exit code 1 on errors.
- `dvr schema`: discoverable catalogs that fill the API's introspection gap — `clip-properties`, `settings`, `export-formats`, `color-presets`, plus live `render-formats`, `render-codecs`, `render-presets`. Solves "what values are valid for SetSetting?".
- `dvr eval` / `dvr exec` / `dvr repl`: scripting escape hatches with a connected `r = Resolve()` already bound, plus `project`, `timeline`, `dvr` for convenience.
- `dvr clip ls / set / mark / inspect --where "..."`: bulk operations driven by a safe expression evaluator (Python-like syntax restricted to comparisons / boolean ops / arithmetic — no attribute access, no calls). One CLI invocation replaces a Python loop.
- `dvr completion show bash|zsh|fish`: auto-generated shell completion scripts for the entire CLI.
- Spec engine hooks: `hooks.before` / `hooks.after` shell commands that run around `dvr apply`. Makes hook-driven workflows (S3 upload, Slack notification, frame.io push) declarative.

### Documentation
- Clarified that DaVinci Resolve **Studio** is required (Blackmagic restricted external scripting to Studio in v19.1+). Free edition is supported only in `--dry-run` and inspection-only flows.

### Added — domain coverage
- Media domain: `MediaPool`, `Asset` / `MediaPoolItem`, `Bin`, `MediaStorage` with bins, import, relink, proxy linking, auto-sync
- Color domain: `ColorOps` (CDL, LUT export, magic mask, stabilization, smart reframe, versions), `NodeGraph`, `ColorGroup`
- Audio domain: voice isolation, channel mapping introspection, Fairlight presets, audio insertion
- Gallery domain: still albums, PowerGrade albums, import/export
- Fusion (per-clip): `ClipFusion` for add / load / import / export / rename / delete comps
- Takes: `Takes` for take/variant management on a clip
- Interchange: unified import/export covering 21 formats — AAF, EDL (+ CDL/SDL/missing), FCP7 XML, FCPXML 1.8/1.9/1.10, DRT, OTIO, CSV, TAB, ALE, ALE-CDL, Dolby Vision 2.9/4.0/5.1, HDR10 A/B
- Daemon: `dvr serve start/stop/status/methods` with newline-delimited JSON over a Unix socket
- MCP server: `dvr mcp serve` exposes the library as typed tools for LLM agents
- Declarative specs: `dvr plan` and `dvr apply` reconcile YAML/JSON specs against live state, with built-in HDR color presets
- CLI sub-apps for every domain: `dvr media`, `dvr serve`, `dvr mcp`, `dvr plan`, `dvr apply`
- Docs site: mkdocs-material with concept guides, CLI reference, library tour, daemon and MCP guides, declarative-spec walkthrough, auto-generated API reference

## [0.1.0] - 2026-04-24

### Added
- Initial scaffold: connection layer, error system, Resolve / Project / Timeline / Render core classes
- CLI entry point with structured output (JSON / table / YAML)
- Inspection-first object model
