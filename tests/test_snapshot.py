"""Tests for snapshot persistence (no Resolve required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dvr import snapshot


@pytest.fixture(autouse=True)
def isolated_snapshot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DVR_SNAPSHOT_DIR", str(tmp_path))
    return tmp_path


def test_save_and_load_round_trip() -> None:
    snap = snapshot.Snapshot(
        name="alpha",
        project="MyShow",
        captured_at="2026-04-25T00:00:00Z",
        data={"settings": {"a": "1"}, "timelines": []},
    )
    snapshot.save(snap)
    loaded = snapshot.load("alpha")
    assert loaded.name == "alpha"
    assert loaded.project == "MyShow"
    assert loaded.data == {"settings": {"a": "1"}, "timelines": []}


def test_list_snapshots_orders_newest_first() -> None:
    a = snapshot.Snapshot(name="a", project="P", captured_at="2026-04-24T00:00:00Z")
    b = snapshot.Snapshot(name="b", project="P", captured_at="2026-04-25T00:00:00Z")
    snapshot.save(a)
    snapshot.save(b)
    names = [s.name for s in snapshot.list_snapshots()]
    assert names == ["b", "a"]


def test_delete_snapshot() -> None:
    snap = snapshot.Snapshot(name="zeta", project="P", captured_at="2026-04-25T00:00:00Z")
    snapshot.save(snap)
    assert any(s.name == "zeta" for s in snapshot.list_snapshots())
    snapshot.delete("zeta")
    assert not any(s.name == "zeta" for s in snapshot.list_snapshots())


def test_load_missing_snapshot_raises() -> None:
    from dvr import errors

    with pytest.raises(errors.DvrError):
        snapshot.load("does_not_exist")
