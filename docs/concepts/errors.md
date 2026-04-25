# Errors and diagnostics

The Resolve scripting API's worst trait is silent failure. Most calls return `None` or `False` with no context: was the project missing? was the page wrong? did the format/codec combination fail validation? You don't know.

`dvr` fixes this. Every wrapped call decodes failures into a structured exception with three fields beyond the message:

- **`cause`** — the most likely underlying reason, computed from read-back state
- **`fix`** — how to recover, often a code snippet
- **`state`** — a snapshot of relevant state at the moment of failure

## Anatomy

```python
from dvr import Resolve

r = Resolve()
try:
    r.render.submit(target_dir="/tmp/out")
except r.errors.RenderError as exc:
    print(exc.message)   # "Could not add a render job."
    print(exc.cause)     # "AddRenderJob produced no new entry. ..."
    print(exc.fix)       # "Verify a timeline is loaded and the format/codec pair is supported."
    print(exc.state)     # {"queue_size": 0, "format_codec": {...}}
```

The default `str(exc)` rendering is multi-line and human-readable:

```
Could not add a render job.
  Cause: AddRenderJob produced no new entry. Common causes: no current timeline; format/codec invalid; an unsaved render is open.
  Fix:   Verify a timeline is loaded and the format/codec pair is supported.
  State: {'queue_size': 0, 'format_codec': {'format': 'mov', 'codec': 'ProRes4444XQ'}}
```

## Exception hierarchy

All errors inherit from `dvr.errors.DvrError`:

| Exception | When raised |
|-----------|-------------|
| `ConnectionError` | Could not reach Resolve within the timeout |
| `NotInstalledError` | `fusionscript` library not found on disk |
| `ScriptingDisabledError` | Resolve is running but external scripting is off |
| `ProjectError` | A project-level operation failed |
| `TimelineError` | A timeline-level operation failed |
| `TrackError` | A track operation failed |
| `ClipError` | A clip / TimelineItem operation failed |
| `MediaError` | A media import / relink / proxy operation failed |
| `RenderError` | A render submission, monitoring, or completion failed |
| `SettingsError` | An invalid project / timeline setting key/value |
| `ColorError` | A color-page operation failed |
| `FusionError` | A Fusion-comp wrap / unwrap operation failed |
| `InterchangeError` | An EDL / AAF / FCPXML / OTIO operation failed |
| `SpecError` | A declarative spec failed to parse or reconcile |

## CLI behavior

When the CLI catches a `DvrError`, it serializes it according to the chosen output format:

=== "JSON (piped)"
    ```json
    {
      "type": "RenderError",
      "message": "Could not add a render job.",
      "cause": "AddRenderJob produced no new entry...",
      "fix": "Verify a timeline is loaded...",
      "state": {"queue_size": 0}
    }
    ```

=== "Table (TTY)"
    ```
    error: Could not add a render job.
      Cause: AddRenderJob produced no new entry...
      Fix:   Verify a timeline is loaded...
    ```

The exit code is always `1` on any `DvrError`.

## For LLM agents

The structured fields are designed to let agents recover deterministically. Branch on `error.type`, read the `cause` and `fix`, and chain the next call. The MCP server returns the same `to_dict()` shape inside tool responses.
