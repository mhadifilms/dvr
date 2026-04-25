"""Tests for the safe `--where` expression evaluator."""

from __future__ import annotations

import pytest

from dvr.cli.commands.clip import _compile_where
from dvr.errors import DvrError


class _FakeClip:
    def __init__(
        self,
        name: str,
        track_index: int,
        track_type: str,
        duration: int,
        start: int = 0,
        end: int = 0,
        enabled: bool = True,
    ) -> None:
        self.name = name
        self.track_index = track_index
        self.track_type = track_type
        self.duration = duration
        self.start = start
        self.end = end
        self.enabled = enabled


def test_simple_comparison() -> None:
    pred = _compile_where("track_index == 2")
    assert pred(_FakeClip("a", 2, "video", 24)) is True
    assert pred(_FakeClip("b", 1, "video", 24)) is False


def test_logical_and() -> None:
    pred = _compile_where("track_index == 2 and duration > 24")
    assert pred(_FakeClip("a", 2, "video", 30)) is True
    assert pred(_FakeClip("b", 2, "video", 12)) is False


def test_logical_or() -> None:
    pred = _compile_where("duration < 12 or duration > 100")
    assert pred(_FakeClip("a", 1, "video", 5)) is True
    assert pred(_FakeClip("b", 1, "video", 200)) is True
    assert pred(_FakeClip("c", 1, "video", 50)) is False


def test_chained_comparison() -> None:
    pred = _compile_where("12 <= duration <= 48")
    assert pred(_FakeClip("a", 1, "video", 24)) is True
    assert pred(_FakeClip("b", 1, "video", 100)) is False


def test_string_comparison() -> None:
    pred = _compile_where("track_type == 'video'")
    assert pred(_FakeClip("a", 1, "video", 24)) is True
    assert pred(_FakeClip("b", 1, "audio", 24)) is False


def test_unknown_variable_raises() -> None:
    pred = _compile_where("nonexistent > 0")
    with pytest.raises(DvrError):
        pred(_FakeClip("a", 1, "video", 24))


def test_invalid_syntax_raises() -> None:
    with pytest.raises(DvrError):
        _compile_where("track_index ==")


def test_attribute_access_disallowed() -> None:
    """`__class__`, `__import__`, etc. should not be reachable through where."""
    with pytest.raises(DvrError):
        # ast.Attribute is not in our allow-list, so this rejects.
        pred = _compile_where("name.upper() == 'FOO'")
        pred(_FakeClip("a", 1, "video", 24))
