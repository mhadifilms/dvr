"""Tests for schema-derived, validated project settings."""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.project import Project, _setting_keys

from .conftest import MockNode


def _project() -> tuple[Project, MockNode]:
    raw = MockNode(
        "Project",
        {
            "SetSetting": lambda key, value: True,
            "GetSetting": lambda key=None: "davinciYRGB" if key == "colorScienceMode" else None,
        },
    )
    return Project(raw, MockNode("PM")), raw


def test_setting_keys_derived_from_schema_catalog() -> None:
    keys = _setting_keys()
    # Manual aliases survive.
    assert keys["timeline_frame_rate"] == "timelineFrameRate"
    # Auto-derived from schema.PROJECT_SETTINGS / spec.CAPTURED_SETTINGS.
    assert keys["timeline_working_luminance_mode"] == "timelineWorkingLuminanceMode"
    assert keys["input_drt"] == "inputDRT"
    assert keys["hdr_mastering_on"] == "hdrMasteringOn"


def test_settings_read_and_write_via_snake_case() -> None:
    proj, raw = _project()
    assert proj.settings.color_science_mode == "davinciYRGB"
    proj.settings.color_science_mode = "acescct"
    assert ("SetSetting", ("colorScienceMode", "acescct"), {}) in raw.calls


def test_settings_reject_invalid_enum_value_before_resolve() -> None:
    proj, raw = _project()
    with pytest.raises(errors.SettingsError) as exc:
        proj.settings.color_science_mode = "not-a-mode"
    assert "davinciYRGB" in (exc.value.fix or "")
    # Resolve was never asked to store the bad value.
    assert not any(c[0] == "SetSetting" for c in raw.calls)


def test_settings_normalize_bools_for_bool_string_keys() -> None:
    proj, raw = _project()
    proj.settings.hdr_mastering_on = True
    assert ("SetSetting", ("hdrMasteringOn", "1"), {}) in raw.calls
    proj.settings.hdr_mastering_on = False
    assert ("SetSetting", ("hdrMasteringOn", "0"), {}) in raw.calls


def test_settings_reject_garbage_bool_string() -> None:
    proj, _raw = _project()
    with pytest.raises(errors.SettingsError):
        proj.settings.hdr_mastering_on = "maybe"


def test_settings_describe_returns_schema_metadata() -> None:
    proj, _raw = _project()
    meta = proj.settings.describe("color_science_mode")
    assert meta["key"] == "colorScienceMode"
    assert "acescct" in meta["values"]
    # Raw keys work too, and unknown keys degrade gracefully.
    assert proj.settings.describe("colorScienceMode")["key"] == "colorScienceMode"
    assert proj.settings.describe("someUnknownKey") == {"key": "someUnknownKey"}


def test_unknown_settings_pass_through_unvalidated() -> None:
    proj, raw = _project()
    proj.settings.some_custom_key = "anything"
    assert ("SetSetting", ("some_custom_key", "anything"), {}) in raw.calls
