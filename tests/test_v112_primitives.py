"""Tests for the v1.1.2 reliability primitives:

- ``RenderNamespace.watch`` terminating on synthetic 100% completion when
  Resolve never flips ``JobStatus`` to ``Complete`` (image-sequence renders).
- ``RenderNamespace.clear`` bounded per-job deletion with a timeout error.
- ``RenderNamespace.status`` namespace-level normalized snapshot.
- ``Project.setting_context`` scoped setting flips with restoration.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from dvr import errors
from dvr.project import Project
from dvr.render import RenderJob, RenderNamespace
from dvr.resolve import Resolve
from tests.conftest import MockNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_render_namespace(
    *,
    job_id: str = "job1",
    output_filename: str = "/tmp/out/seq_v001.exr",
    statuses: list[dict] | None = None,
    is_rendering_sequence: list[bool] | None = None,
    queue_after_clear: list[list[dict]] | None = None,
):
    """Build a RenderNamespace wired against a mock project + Resolve.

    ``statuses`` is a list of ``GetRenderJobStatus`` payloads; the last entry
    is sticky. ``is_rendering_sequence`` drives ``IsRenderingInProgress``;
    sticky on the last entry. ``queue_after_clear`` lets a test simulate
    ``DeleteAllRenderJobs`` / ``DeleteRenderJob`` semantics: each call to
    ``GetRenderJobList`` after the first delete pops one entry.
    """
    statuses = list(statuses or [{"JobStatus": "Complete", "CompletionPercentage": 100}])
    is_rendering = list(is_rendering_sequence or [False])
    queue_states = list(queue_after_clear or [[{"JobId": job_id, "OutputFilename": output_filename}]])

    def get_status(_jid):
        if len(statuses) > 1:
            return statuses.pop(0)
        return statuses[0]

    def is_rendering_now():
        if len(is_rendering) > 1:
            return is_rendering.pop(0)
        return is_rendering[0]

    def get_queue():
        if len(queue_states) > 1:
            return list(queue_states.pop(0))
        return list(queue_states[0])

    project_raw = MockNode(
        "Proj",
        {
            "GetRenderJobList": get_queue,
            "GetRenderJobStatus": get_status,
            "IsRenderingInProgress": is_rendering_now,
            "DeleteAllRenderJobs": True,
            "DeleteRenderJob": True,
            "StopRendering": True,
        },
    )
    pm = MockNode(
        "PM",
        {
            "GetCurrentProject": project_raw,
            "SaveProject": True,
        },
    )
    raw_resolve = MockNode(
        "Resolve",
        {
            "GetProjectManager": pm,
            "GetProduct": "DaVinci Resolve Studio",
            "GetVersionString": "20.3-mock",
            "OpenPage": True,
        },
    )
    r = Resolve.__new__(Resolve)
    r._raw = raw_resolve  # type: ignore[attr-defined]
    r._project_manager = pm  # type: ignore[attr-defined]
    r._socket_path = None  # type: ignore[attr-defined]

    return RenderNamespace(r), project_raw


# ---------------------------------------------------------------------------
# RenderNamespace.watch — synthetic completion at 100% for image sequences
# ---------------------------------------------------------------------------


def test_watch_emits_synthetic_complete_when_pct_100_and_not_rendering():
    """EXR sequence renders sometimes never flip JobStatus to Complete.
    When the queue reports 100% and IsRenderingInProgress() is False,
    watch() should emit a complete event and stop polling.
    """
    ns, _ = _make_render_namespace(
        statuses=[
            {"JobStatus": "Rendering", "CompletionPercentage": 50},
            {"JobStatus": "Rendering", "CompletionPercentage": 100},
        ],
        is_rendering_sequence=[True, False],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        events = list(ns.watch(["job1"], poll_interval=0.0))

    final = events[-1]
    assert final["type"] == "complete"
    assert final["job_id"] == "job1"
    assert final.get("synthetic") is True


def test_watch_still_emits_failed_for_failure_status():
    """A real Failed status takes priority over the 100% heuristic."""
    ns, _ = _make_render_namespace(
        statuses=[{"JobStatus": "Failed", "CompletionPercentage": 100, "Error": "boom"}],
        is_rendering_sequence=[False],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        events = list(ns.watch(["job1"], poll_interval=0.0))

    final = events[-1]
    assert final["type"] == "failed"
    assert final["error"] == "boom"


def test_watch_does_not_synthesize_complete_while_still_rendering():
    """Don't trigger the synthetic-complete path while a render is live."""
    ns, _ = _make_render_namespace(
        statuses=[
            {"JobStatus": "Rendering", "CompletionPercentage": 100},
            {"JobStatus": "Complete", "CompletionPercentage": 100},
        ],
        is_rendering_sequence=[True, False],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        events = list(ns.watch(["job1"], poll_interval=0.0))

    # First poll yields a progress event because IsRenderingInProgress=True;
    # second poll sees the real Complete status.
    assert events[0]["type"] == "progress"
    assert events[-1]["type"] == "complete"
    assert events[-1].get("synthetic") is not True


# ---------------------------------------------------------------------------
# RenderJob.wait — same image-sequence stuck-at-100 fallback
# ---------------------------------------------------------------------------


def test_wait_returns_cleanly_when_stuck_at_100_and_not_rendering():
    ns, _ = _make_render_namespace(
        statuses=[{"JobStatus": "Rendering", "CompletionPercentage": 100}],
        is_rendering_sequence=[False],
    )
    job = RenderJob(ns, "job1")

    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        result = job.wait(poll_interval=0.0)

    assert result is job


# ---------------------------------------------------------------------------
# RenderNamespace.clear — bounded queue cleanup
# ---------------------------------------------------------------------------


def test_clear_returns_immediately_when_queue_empty():
    ns, project_raw = _make_render_namespace(queue_after_clear=[[]])
    ns.clear()
    assert not any(c[0] == "DeleteAllRenderJobs" for c in project_raw.calls)


def test_clear_falls_back_to_per_job_delete_when_bulk_silently_fails():
    """Some Resolve builds drop DeleteAllRenderJobs after EXR jobs.
    The fallback path must hit DeleteRenderJob for each remaining job
    and return cleanly once the queue empties.
    """
    queue_states = [
        [{"JobId": "a"}, {"JobId": "b"}],  # initial check
        [{"JobId": "a"}, {"JobId": "b"}],  # still there after DeleteAllRenderJobs
        [],  # after per-job DeleteRenderJob calls
    ]
    ns, project_raw = _make_render_namespace(
        queue_after_clear=queue_states,
        is_rendering_sequence=[False],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        ns.clear(timeout=2.0, poll_interval=0.0)

    delete_calls = [c for c in project_raw.calls if c[0] == "DeleteRenderJob"]
    assert {c[1][0] for c in delete_calls} == {"a", "b"}


def test_clear_raises_when_render_in_progress():
    ns, _ = _make_render_namespace(
        queue_after_clear=[[{"JobId": "a"}]],
        is_rendering_sequence=[True],
    )

    with pytest.raises(errors.RenderError) as ctx:
        ns.clear()

    assert "in progress" in ctx.value.message.lower()


def test_clear_raises_when_deletion_stalls_past_timeout():
    """If both bulk and per-job delete are silently dropped, surface a
    structured RenderError listing the stuck job IDs instead of hanging.
    """
    stuck_queue = [{"JobId": "stuck1"}, {"JobId": "stuck2"}]
    ns, _ = _make_render_namespace(
        queue_after_clear=[stuck_queue],
        is_rendering_sequence=[False],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None), pytest.raises(errors.RenderError) as ctx:
        ns.clear(timeout=0.0, poll_interval=0.0)

    err = ctx.value
    assert "timed out" in err.message.lower()
    assert set(err.state.get("remaining_job_ids", [])) == {"stuck1", "stuck2"}


# ---------------------------------------------------------------------------
# RenderNamespace.status — namespace-level normalized snapshot
# ---------------------------------------------------------------------------


def test_status_returns_normalized_payload():
    ns, _ = _make_render_namespace(
        statuses=[
            {
                "JobStatus": "Rendering",
                "CompletionPercentage": 42,
                "EstimatedTimeRemainingInMs": 60000,
            }
        ],
    )

    snap = ns.status("job1")

    assert snap["id"] == "job1"
    assert snap["status"] == "Rendering"
    assert snap["percent"] == 42
    assert snap["progress"] == pytest.approx(0.42)
    assert snap["eta_seconds"] == pytest.approx(60.0)
    assert snap["error"] is None
    assert snap["is_finished"] is False


def test_status_marks_finished_terminal_states():
    ns, _ = _make_render_namespace(
        statuses=[{"JobStatus": "Failed", "CompletionPercentage": 73, "Error": "disk full"}],
    )
    snap = ns.status("job1")
    assert snap["status"] == "Failed"
    assert snap["error"] == "disk full"
    assert snap["is_finished"] is True


# ---------------------------------------------------------------------------
# Project.setting_context — scoped setting flips
# ---------------------------------------------------------------------------


def _make_project_with_settings_storage(initial: dict[str, str]) -> tuple[Project, dict[str, str]]:
    storage: dict[str, str] = dict(initial)

    raw = MockNode(
        "Proj",
        {
            "GetName": "S",
            "GetSetting": lambda key=None: storage.get(key, ""),
            "SetSetting": lambda key, value: (storage.__setitem__(key, value) or True),
        },
    )
    manager = MockNode("PM", {"SaveProject": True})
    return Project(raw, manager), storage


def test_setting_context_restores_previous_value():
    project, storage = _make_project_with_settings_storage({"colorAcesODT": "Rec.709 Gamma 2.4"})

    with project.setting_context("colorAcesODT", "Rec.709 BT.1886") as previous:
        assert previous == "Rec.709 Gamma 2.4"
        assert storage["colorAcesODT"] == "Rec.709 BT.1886"

    assert storage["colorAcesODT"] == "Rec.709 Gamma 2.4"


def test_setting_context_restores_on_exception():
    project, storage = _make_project_with_settings_storage({"timelineFrameRate": "24.0"})

    with pytest.raises(RuntimeError), project.setting_context("timelineFrameRate", "60.0"):
        assert storage["timelineFrameRate"] == "60.0"
        raise RuntimeError("boom")

    assert storage["timelineFrameRate"] == "24.0"


def test_setting_context_propagates_initial_set_failure():
    """If the initial set_setting fails, no restore is attempted and the
    original SettingsError reaches the caller.
    """

    raw = MockNode(
        "Proj",
        {
            "GetName": "S",
            "GetSetting": lambda key=None: "old",
            "SetSetting": lambda key, value: False,
        },
    )
    manager = MockNode("PM", {"SaveProject": True})
    project = Project(raw, manager)

    with pytest.raises(errors.SettingsError):
        with project.setting_context("badKey", "newValue"):
            pytest.fail("body should not run when initial set fails")
