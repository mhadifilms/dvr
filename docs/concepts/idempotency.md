# Idempotency

Operations like "create a project" or "create a timeline" should be safe to repeat. The raw Resolve API doesn't think so — calling `CreateProject("Foo")` twice returns `None` the second time, with no way to tell whether the project exists or something else went wrong.

`dvr` exposes `ensure()` methods that follow get-or-create semantics:

```python
project = r.project.ensure("MyShow")              # creates if missing, loads if present
timeline = project.timeline.ensure("Edit_v2")     # same
bin_obj = project.media.ensure_bin("VFX")         # same
```

Re-running any script that uses `ensure()` is always safe. There are no "already exists" errors and no order-of-operations bugs.

## Context managers

For temporarily switching state, use the `use()` context managers:

```python
with r.project.use("MyShow") as project:
    with project.timeline.use("Edit_v2") as tl:
        # ... work in this project / timeline ...
        pass
# previous project + timeline restored on exit
```

Both managers handle the case where the target doesn't exist yet (they call `ensure()`).

## Declarative reconciliation

The strongest expression of idempotency is `dvr apply` — see [Declarative specs](../spec.md). You describe the desired state in YAML, and `dvr` computes the diff against the live Resolve state and applies only the deltas. Re-applying the same spec is a no-op.

## Why it matters

For pipelines and LLM agents, idempotency is the difference between "the script worked" and "the script worked, and re-running it doesn't break anything if it gets retried." If you wrap your `dvr` calls in retry logic for transient connection issues, you don't need to know whether the last call succeeded or not.
