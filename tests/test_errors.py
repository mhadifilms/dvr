"""Tests for the diagnostic error system.

These tests do not require a running Resolve instance.
"""

from __future__ import annotations

from dvr import errors


def test_dvr_error_carries_diagnostic_fields() -> None:
    err = errors.DvrError(
        "could not do thing",
        cause="thing was unavailable",
        fix="restart the thing",
        state={"thing": "broken"},
    )
    rendered = str(err)
    assert "could not do thing" in rendered
    assert "Cause: thing was unavailable" in rendered
    assert "Fix:   restart the thing" in rendered
    assert "thing" in rendered  # state included


def test_dvr_error_to_dict_round_trip() -> None:
    err = errors.RenderError(
        "render failed",
        cause="codec not set",
        fix="run `dvr render codecs mov`",
        state={"queue_size": 0},
    )
    payload = err.to_dict()
    assert payload["type"] == "RenderError"
    assert payload["message"] == "render failed"
    assert payload["cause"] == "codec not set"
    assert payload["fix"] == "run `dvr render codecs mov`"
    assert payload["state"] == {"queue_size": 0}


def test_dvr_error_minimal_renders_only_message() -> None:
    err = errors.DvrError("just the message")
    assert str(err) == "just the message"


def test_specialized_errors_inherit_dvr_error() -> None:
    for cls in (
        errors.ConnectionError,
        errors.NotInstalledError,
        errors.ScriptingDisabledError,
        errors.ProjectError,
        errors.TimelineError,
        errors.TrackError,
        errors.ClipError,
        errors.MediaError,
        errors.RenderError,
        errors.SettingsError,
        errors.ColorError,
        errors.FusionError,
        errors.InterchangeError,
        errors.SpecError,
    ):
        assert issubclass(cls, errors.DvrError)
