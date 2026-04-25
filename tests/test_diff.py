"""Tests for the diff engine (no Resolve required)."""

from __future__ import annotations

from dvr import diff


def test_compare_identical_dicts_is_empty() -> None:
    result = diff.compare({"a": 1, "b": 2}, {"a": 1, "b": 2})
    assert result.empty


def test_compare_changed_value() -> None:
    result = diff.compare({"a": 1}, {"a": 2})
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.op == "changed"
    assert change.path == "a"
    assert change.left == 1
    assert change.right == 2


def test_compare_added_and_removed_keys() -> None:
    result = diff.compare({"a": 1, "b": 2}, {"a": 1, "c": 3})
    paths = {(c.op, c.path) for c in result.changes}
    assert ("removed", "b") in paths
    assert ("added", "c") in paths


def test_compare_nested_dicts() -> None:
    left = {"tracks": {"video": {"v1": {"clips": 1}}}}
    right = {"tracks": {"video": {"v1": {"clips": 2}}}}
    result = diff.compare(left, right)
    assert len(result.changes) == 1
    assert result.changes[0].path == "tracks.video.v1.clips"


def test_compare_keyed_lists_align_by_name() -> None:
    left = [{"name": "v1", "n": 1}, {"name": "v2", "n": 2}]
    right = [{"name": "v2", "n": 2}, {"name": "v1", "n": 99}]  # reordered + v1 changed
    result = diff.compare(left, right)
    # Reordering should not produce noise.
    assert all(c.op == "changed" for c in result.changes)
    assert len(result.changes) == 1


def test_diff_summary() -> None:
    result = diff.compare(
        {"a": 1, "b": 2, "c": 3},
        {"a": 1, "b": 99, "d": 4},
    )
    payload = result.to_dict()
    summary = payload["summary"]
    assert summary["changed"] == 1
    assert summary["added"] == 1
    assert summary["removed"] == 1
    assert summary["total"] == 3
