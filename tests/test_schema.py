"""Tests for static schema catalogs (no Resolve required)."""

from __future__ import annotations

import pytest

from dvr import schema


def test_clip_properties_static() -> None:
    catalog = schema.get_topic("clip-properties")
    assert "Pan" in catalog
    assert catalog["CompositeMode"]["type"] == "enum"
    assert "Difference" in catalog["CompositeMode"]["values"]


def test_settings_static() -> None:
    catalog = schema.get_topic("settings")
    assert "colorScienceMode" in catalog
    assert "davinciYRGBColorManagedv2" in catalog["colorScienceMode"]["values"]


def test_export_formats_static() -> None:
    formats = schema.get_topic("export-formats")
    assert "fcpxml-1.10" in formats
    assert "edl" in formats
    assert "aaf" in formats


def test_color_presets_static() -> None:
    presets = schema.get_topic("color-presets")
    assert "rec2020_pq_4000" in presets
    assert "rec709_gamma24" in presets


def test_unknown_topic_raises() -> None:
    with pytest.raises(ValueError):
        schema.get_topic("does-not-exist")
