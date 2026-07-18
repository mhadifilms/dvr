"""Tests for the record/replay (VCR) harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from dvr import errors, vcr

from .conftest import MockResolve


def _record_session(cassette: Path) -> None:
    rec = vcr.wrap_recording(MockResolve(), cassette)
    assert rec.GetVersionString() == "20.3.1-mock"
    pm = rec.GetProjectManager()
    project = pm.GetCurrentProject()
    assert project.GetName() == "MockProject"
    timeline = project.GetCurrentTimeline()
    assert timeline.GetName() == "MockTimeline"


def test_record_then_replay_serves_identical_responses(tmp_path: Path) -> None:
    cassette = tmp_path / "session.jsonl"
    _record_session(cassette)

    rep = vcr.replay_raw(cassette)
    assert rep.GetVersionString() == "20.3.1-mock"
    pm = rep.GetProjectManager()
    project = pm.GetCurrentProject()
    assert project.GetName() == "MockProject"
    timeline = project.GetCurrentTimeline()
    assert timeline.GetName() == "MockTimeline"


def test_recording_unwraps_nested_handle_arguments(tmp_path: Path) -> None:
    class Target:
        def __init__(self) -> None:
            self.received = None

        def Child(self):
            return object()

        def Submit(self, payload, *, options):
            self.received = (payload, options)
            return True

    cassette = tmp_path / "nested.jsonl"
    target = Target()
    rec = vcr.wrap_recording(target, cassette)
    child = rec.Child()

    assert rec.Submit(
        [{"mediaPoolItem": child}],
        options={"linked": (child,)},
    )
    payload, options = target.received
    assert payload[0]["mediaPoolItem"] is child._vcr_target
    assert options["linked"][0] is child._vcr_target


def test_replay_diverges_loudly(tmp_path: Path) -> None:
    cassette = tmp_path / "session.jsonl"
    _record_session(cassette)

    rep = vcr.replay_raw(cassette)
    rep.GetVersionString()
    with pytest.raises(errors.DvrError) as exc:
        rep.GetVersionString()  # recorded only once — cassette exhausted
    assert "diverged" in exc.value.message


def test_missing_cassette_raises() -> None:
    with pytest.raises(errors.DvrError):
        vcr.replay_raw("/definitely/not/a/cassette.jsonl")


def test_resolve_from_cassette_drives_wrappers(tmp_path: Path) -> None:
    cassette = tmp_path / "session.jsonl"

    # Record through the real library wrappers (as DVR_RECORD would).
    from dvr.resolve import Resolve

    raw = vcr.wrap_recording(MockResolve(), cassette)
    r = Resolve.__new__(Resolve)
    r._raw = raw
    r._project_manager = raw.GetProjectManager()
    assert r.app.version == "20.3.1-mock"
    assert r.project.current is not None
    assert r.project.current.name == "MockProject"
    names = r.project.list()
    assert names == ["MockProject"]

    # Replay the same library code with no mock and no Resolve.
    r2 = vcr.resolve_from_cassette(cassette)
    assert r2.app.version == "20.3.1-mock"
    assert r2.project.current is not None
    assert r2.project.current.name == "MockProject"
    assert r2.project.list() == ["MockProject"]
