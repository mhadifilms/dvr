# Changelog

All notable changes to `dvr` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Transparent daemon forwarding.** When a `dvr serve` daemon is running,
  every ordinary CLI command automatically routes through it over the Unix
  socket and reuses its persistent Resolve connection — ~50ms per command
  instead of a 2-3s cold connect, with identical stdout/stderr/exit codes.
  The daemon gained a `cli` wire method (`daemon.run_cli()`); interactive,
  streaming, and daemon-lifecycle commands still run locally.
  `DVR_NO_DAEMON=1` opts out; `DVR_DAEMON=auto` auto-spawns the daemon on
  first use.
- **Transactional + verified apply.** `dvr apply --transactional` snapshots
  the project before mutating and restores it automatically when any action
  fails (the error reports the snapshot and rollback). `--verify` reads
  every setting back after writing and fails loudly when Resolve silently
  ignores a value. Both are exposed on the `apply_spec` MCP tool and
  `dvr.spec.apply()`. A general-purpose `Resolve.transaction()` context
  manager backs the same behavior for library code.
- **Higher-fidelity specs and snapshots.** Specs now declare media-pool
  `bins` (nested `A/B/C` paths) and per-timeline minimum `tracks` counts;
  `dvr plan` / `dvr apply` / `dvr diff spec` all understand them. Snapshots
  (v2) capture the bin tree and per-timeline track counts and restore both.
- **`dvr spec export`** (library: `dvr.spec.from_live()`, MCP:
  `spec_export`) — reverse-engineer a spec from live project state so an
  existing project can be adopted into declarative management, terraform
  import style.
- **Agent-oriented MCP surface.** The server now publishes MCP *resources*
  (`dvr://inspect`, `dvr://timeline/current`, `dvr://media/bins`,
  `dvr://render/queue`, `dvr://doctor`, `dvr://schema/<topic>`) so clients
  can read live state instead of spending tool calls on it; a
  `timeline_assemble` workflow tool that imports media and assembles a
  rough cut in one call; and `render_wait`, which blocks until a render job
  finishes instead of forcing agents to poll `render_status`.
- **Record/replay harness (`dvr.vcr`).** Set `DVR_RECORD=<path>` to capture
  every fusionscript call (method, args, result) to a JSONL cassette while
  running against a real Resolve; replay it later with
  `vcr.resolve_from_cassette(path)` on machines without Resolve. Replays
  raise a structured error on divergence — mock-free regression tests from
  real recorded behavior.
- **Typed, validated settings.** `project.settings` attribute names are now
  derived from the `dvr.schema` catalogs (every `PROJECT_SETTINGS` /
  `CAPTURED_SETTINGS` key gets a snake_case attribute), enum and
  bool-string values are validated *before* the write (invalid values raise
  `SettingsError` with the valid list; bools normalize to `"0"`/`"1"`), and
  `settings.describe(name)` returns the schema metadata for any setting.
- `dvr doctor` CLI command — the setup diagnostics that previously existed
  only as an MCP tool. Backed by a new shared `dvr.doctor.diagnose()`
  library function (static probe by default, `--probe` attempts a live
  connection) and new public `connection.platform_paths()` /
  `connection.resolve_process_running()` helpers.
- `dvr media scan` CLI command — preview which media files a bulk import
  would pick up, without touching Resolve. Backed by a new
  `dvr.media.scan_media_files()` library function (also used by the
  `media_scan` MCP tool), plus `media_kind_for_path()`.
- `MediaPool.find_folder_path()` / `MediaPool.ensure_folder_path()` —
  resolve or create nested `"A/B/C"` bin paths. Previously this logic
  lived only inside the MCP server; the CLI (`dvr media ls/mkbin/import`)
  and MCP now share it, so nested bin paths work everywhere.
- `ProjectNamespace.require_current()` — returns the current project or
  raises a structured `ProjectError`, replacing ad-hoc "no project loaded"
  checks across the CLI and MCP server.
- `daemon.methods()` — public accessor for the daemon RPC allow-list
  (the `dvr serve methods` command no longer reads a private attribute).
- On-screen text customization. `dvr` could only drop a raw Fusion node;
  it now inserts and fully styles titles across every interface.
  - Library: `Timeline.insert_title(title="Text+", *, fusion=True, ...)`
    inserts a (Fusion) title at the playhead and styles it in one call, and
    `TimelineItem.text` (`ItemText`) reads/sets the Text+ string, font,
    style, size, color (hex / name / `[r,g,b]`), opacity, tracking, line
    spacing, position, and horizontal/vertical alignment. New
    `FusionTool.set_point()` and `FusionComp.text_tools()` helpers back it.
  - CLI: `dvr timeline add-title` and `dvr clip text` (bulk-style filtered
    clips, skipping non-text items).
  - MCP tools: `timeline_add_title` and `clip_set_text`.
  - Declarative specs: a timeline-level `titles` list inserts and styles
    titles, matched by their text so re-running `dvr apply` updates styling
    in place instead of stacking duplicates.
  - `TimelineItem.is_text` reports whether an item carries an editable Text+
    tool.
- Text-to-speech customization: `--speed`, `--pitch`, and `--filename` are
  now exposed on `dvr project generate-speech` and the
  `project_generate_speech` MCP tool (the library already accepted the full
  settings dict).
- Subtitle generation reached the CLI and MCP server: `dvr timeline
  subtitles` and the `timeline_create_subtitles` tool wrap
  `Timeline.create_subtitles_from_audio` with its language, characters-per-line,
  line-break, and preset options.
- Broad expansion of the wrapped DaVinci Resolve scripting surface so far
  reachable only through `.raw`/`eval`:
  - App: `app.fusion` (the global Fusion handle), UI layout presets
    (`save_layout`/`load_layout`/`update_layout`/`import_layout`/
    `export_layout`/`delete_layout`), and Color-page `keyframe_mode`.
  - ProjectManager: `restore()`, database management (`current_database`,
    `databases`, `set_current_database`), project-database folder navigation
    (`create_folder`/`delete_folder`/`open_folder`/`goto_root_folder`/
    `goto_parent_folder`/`current_folder`), and DaVinci Cloud projects
    (`create_cloud_project`/`import_cloud_project`/`restore_cloud_project`).
  - Project: color groups (`color_groups`/`add_color_group`/
    `delete_color_group`), `export_current_frame_as_still`, Quick Export
    (`quick_export_presets`/`quick_export`), `load_burn_in_preset`, and
    `unique_id`.
  - Render: render mode get/set, `resolutions()`, and `refresh_lut_list()`.
  - Media: clip/timeline mattes (`add_clip_mattes`/`add_timeline_mattes`/
    `clip_mattes`/`timeline_mattes`/`delete_clip_mattes`), `move_folders`,
    `create_stereo_clip`, `export_metadata`, `import_folder_from_file` (.drb),
    and `unique_id` on pool/folder/clip; clip `media_id`, third-party
    metadata, and marker custom-data accessors.
  - Timeline: settable `start_timecode`, `insert_generator` (standard /
    Fusion / OFX), `insert_fusion_composition`, `create_fusion_clip`,
    `import_into`, `set_clips_linked`, `current_video_item`,
    `current_clip_thumbnail`, `grab_still`/`grab_all_stills`,
    `convert_to_stereo`, `analyze_dolby_vision`, `unique_id`, and marker
    custom-data on `MarkerCollection`.
  - TimelineItem: `left_offset`/`right_offset`/`handles`, `fusion_comp_count`,
    `unique_id`, `update_sidecar`, stereo convergence / floating-window
    params, and marker custom-data accessors.
  - CLI: `dvr timeline start-tc | add-generator | grab-stills | import-into`,
    `dvr render mode | resolutions | refresh-luts`, `dvr project color-groups |
    export-still | quick-export`, and `dvr media export-metadata | import-bin`.
  - MCP: `timeline_set_start_timecode`, `timeline_add_generator`,
    `timeline_grab_stills`, `timeline_import_into`, `render_mode`,
    `render_resolutions`, `render_refresh_luts`, `project_color_groups`,
    `project_export_still`, `media_export_metadata`, and `media_import_bin`.

### Fixed

- `dvr serve start --no-launch` (background mode) spawned a broken child
  command (`python -m --no-launch dvr ...`); the global flag is now placed
  correctly after `dvr`.
- CLI commands now render *all* library errors as structured output
  (JSON on stderr when piped) instead of Python tracebacks. Previously
  only a handful of top-level commands did; `dvr project list`,
  `dvr media ...`, `dvr render ...` and the rest let `DvrError` escape as
  a traceback. The `dvr` entry point now routes through a central handler,
  and `--format` is honored by error output.
- `dvr media` subcommands raise structured `MediaError`/`ProjectError`
  diagnostics (with `cause`/`fix`/`state`) instead of bare
  `typer.echo(..., err=True)` messages.

### Changed

- The console script entry point moved from `dvr.cli.main:app` to
  `dvr.cli.main:main`, so Ctrl-C and structured error handling behave the
  same for `dvr` and `python -m dvr`.
- The MCP server's `doctor`, `media_scan`, bin-path, and current-project
  helpers now delegate to the shared library implementations above (no
  behavior change to the MCP tools themselves).

## [1.3.0] - 2026-06-25

Editing controls expansion. Additive across the library, CLI, MCP server,
and declarative specs.

### Added

- First-class wrappers for Resolve's documented static
  `TimelineItem.SetProperty` editing surface:
  - Library: `TimelineItem.set_properties()`, `TimelineItem.reset_properties()`,
    `TimelineItem.edit`, and batch helpers on `ItemQuery` for transform,
    crop, composite, retime, and scaling controls.
  - CLI: `dvr clip transform`, `dvr clip crop`, `dvr clip composite`,
    `dvr clip retime`, `dvr clip reset`, and `dvr clip capabilities`.
  - MCP tools: `clip_set_properties`, `clip_transform`, `clip_crop`,
    `clip_reset`, and `clip_capabilities`.
  - Declarative specs: timeline-level `clip_properties` / `clips` operations
    with stable selectors and idempotent property application.
- Expanded `dvr schema clip-properties` to match Resolve's documented
  transform, crop, dynamic zoom, composite, retime, scaling, and resize-filter
  property surface, including enum constants, aliases, defaults, and capability
  metadata.

### Changed

- `dvr clip set` now normalizes friendly aliases and enum names before calling
  Resolve's `SetProperty`, while preserving raw property-key usage.

## [1.2.0] - 2026-06-03

DaVinci Resolve 21 support. Additive across the library, CLI, and MCP
server, with backward compatibility for pre-21 / Free installs.

### Added

- DaVinci Resolve 21 AI/Studio scripting surface, exposed across the
  library, CLI, and MCP server. On older Resolve builds (or the Free
  edition) these raise a structured "requires DaVinci Resolve 21" error
  instead of leaking an `AttributeError`.
  - Library:
    - `Clip` and `Folder`: `classify_audio()`,
      `clear_audio_classification()`, `remove_motion_blur(options)`,
      `analyze_for_intellisearch(...)`, `analyze_for_slate(marker_color)`.
    - `Project`: `reset_intellisearch_analysis()`,
      `generate_speech(settings, timecode)`.
    - `App`: `disable_background_tasks()`
      (`DisableBackgroundTasksForCurrentResolveSession`; safe no-op on
      pre-21 builds).
  - CLI: `dvr media transcribe`, `dvr media classify-audio`,
    `dvr media deblur`, `dvr media analyze {intellisearch|slate}`,
    `dvr project reset-intellisearch`, `dvr project generate-speech`,
    and top-level `dvr disable-background-tasks`. Each targets a bin
    (default: root, recursive) or a single clip via `--clip`.
  - MCP tools: `media_transcribe`, `media_classify_audio`,
    `media_deblur`, `media_analyze`, `project_reset_intellisearch`,
    `project_generate_speech`, `disable_background_tasks`.
- `dvr._wrap.requires_method` — internal helper that resolves a raw
  Resolve method or raises a clear minimum-version error, used to keep
  the new v21 calls backward compatible on older installs.

### Fixed

- `Clip.transcribe()` / `Folder.transcribe()` no longer pass the legacy
  `language` string as the first positional argument to Resolve's
  `TranscribeAudio`. Resolve 21 reinterprets that first positional as
  `useSpeakerDetection`, so a truthy value like `"auto"` silently enabled
  speaker detection. The method now calls `TranscribeAudio()` with no
  args by default and accepts an explicit `use_speaker_detection`
  keyword. `language` is retained for backwards compatibility but has no
  effect on the API call (transcription language is a project setting).

## [1.1.7] - 2026-06-02

Patch release fixing local connection on macOS. No breaking changes.

### Fixed

- `connect()` on macOS now falls back to plain `scriptapp("Resolve")` on
  localhost after trying the machine's LAN IPs. Previously the macOS path
  only attempted LAN-IP connections, so a running, scriptable local Resolve
  that binds its scripting socket to `127.0.0.1` (External scripting = Local),
  or a machine with no usable LAN IP, was unreachable unless remote discovery
  was enabled. Remote `pinghosts` discovery remains gated behind
  `discover_remote`.

## [1.1.4] - 2026-04-30

Patch release focused on simplifying installation for MCP users. No breaking
runtime behavior changes.

### Changed

- MCP support is now included in the default install. `pip install dvr` is
  enough to run `dvr mcp serve`; the old MCP install extra has been removed.

## [1.1.3] - 2026-04-29

Patch release focused on MCP workflow reliability for agent-driven project
cleanup, media-bin addressing, timeline placement, and declarative specs.
No breaking changes.

### Added

- MCP cleanup/discovery tools:
  - `project_delete`
  - `project_settings_get`
  - `timeline_delete`
  - `timeline_rename`
  - `timeline_clear`
  - `media_bin_delete`

### Fixed

- `Spec.parse_spec()` now rejects `project: {name: ...}` and other
  non-string project values instead of stringifying them into accidental
  project names.
- `spec.apply(..., continue_on_error=True)` continues applying remaining
  settings/markers after per-key failures and returns a structured
  `SpecError` summary at the end.
- MCP `media_import`, `media_ls`, and `media_move.source_bin` now accept
  slash-addressed bin paths consistently with `media_bin_ensure`.
- MCP `timeline_append` now fails loudly when Resolve returns a partial
  append, and requires explicit `record_frame` values for non-default
  tracks to avoid silent V2/A2+ placement loss.
- `lint` no longer reports `empty_timeline` for populated timelines whose
  track summaries expose `item_count` rather than the old `clip_count`.
- `schema settings` now documents color-space dependency papercuts such as
  `separateColorSpaceAndGamma` ordering and `colorSpaceOutput` values.

## [1.1.2] - 2026-04-29

Patch release focused on render-queue reliability for image-sequence
(EXR / DPX) deliveries and scoped project-setting ergonomics. No
breaking changes.

### Added

- `Project.setting_context(key, value)` — context manager that flips a
  project setting for the duration of a `with` block and restores the
  previous value on exit (including on exceptions). Prevents settings
  leaks in toolkits that need to flip ACES ODT, frame rate, or other
  scoped values around a single render.
- `RenderNamespace.status(job_id)` — namespace-level normalized status
  snapshot (`{id, status, percent, progress, eta_seconds, output_path,
  error, is_finished}`). Toolkits no longer need to construct their own
  `RenderJob` to read the queue. The MCP `render_status` tool now
  delegates to this method.

### Fixed

- `RenderNamespace.watch()` — now terminates cleanly for image-sequence
  (EXR / DPX) renders that reach `CompletionPercentage == 100` but
  whose `JobStatus` never flips to `Complete`. When the percentage is
  at 100 and `Project.IsRenderingInProgress()` is False, `watch()`
  emits a synthetic `complete` event (tagged `synthetic: True`) and
  stops polling. Failure / cancel paths are unchanged.
- `RenderJob.wait()` — same image-sequence stuck-at-100 fallback;
  returns cleanly instead of stalling until the stall-seconds budget
  expires.
- `RenderNamespace.clear()` — bounded cleanup. Refuses to clear while a
  render is in progress (raises `RenderError`), tries
  `DeleteAllRenderJobs` first, then falls back to per-job
  `DeleteRenderJob` calls until the queue empties or the timeout
  (default 10 s) elapses. On timeout, raises a structured `RenderError`
  listing the stuck job IDs instead of blocking forever — toolkits can
  now safely call `r.render.clear()` between shots.

## [1.1.1] - 2026-04-29

Patch release focused on MCP reliability and agent-safe editorial primitives.
No breaking changes.

### Added — MCP server

- `dvr[mcp]` extras install path and `dvr mcp serve` stdio server are now
  documented as the recommended integration for Claude, Cursor, and other
  MCP-compatible clients.
- Bundled MCP branding assets are exposed through the `version` and `doctor`
  tool responses as `brand.logo` and `brand.icon`.
- `dvr mcp tools` introspects every exposed MCP tool, including full JSON
  schemas with `--detail`.
- `dvr mcp install`, `dvr mcp install-claude`, `dvr mcp install-cursor`, and
  `dvr mcp install-claude-code` register the MCP server with safe defaults
  (`--no-launch --timeout 5`) while preserving existing client config.
- New typed MCP tools:
  - `version`, `doctor`, and `reconnect` for setup and diagnostics.
  - `media_scan` for filesystem media discovery without Resolve.
  - `media_bin_ensure` and `media_move` for reusable media-pool organization.
  - `timeline_append` for track-targeted timeline placement without `eval`.
  - `marker_add`, `clip_where`, `render_clear`, and `apply_spec`.

### Improved

- The documentation site and README now use the project logo, including
  favicon, Apple touch icon, and web-manifest assets for browsers.
- MCP errors now return proper `CallToolResult(isError=True)` responses while
  preserving structured `DvrError` payloads (`type`, `message`, `cause`,
  `fix`, `state`).
- MCP `doctor` is fast by default and only attempts a live Resolve connection
  when `probe=true`.
- MCP connection failures are cached briefly so repeated failed tool calls do
  not hammer Resolve's `fusionscript` bridge or accumulate stuck timeout
  threads.
- `eval` remains available as an explicit escape hatch, but is disabled unless
  `DVR_MCP_ENABLE_EVAL=1` is set.

### Fixed

- Resolve 21 beta compatibility: `App.product` now uses `GetProductName()`
  first and falls back to older `GetProduct()` builds.
- MCP startup and live-tool failure paths now fail fast enough for Claude and
  Cursor tool-call timeouts instead of appearing to hang.

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
