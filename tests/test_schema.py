"""Tests for static schema catalogs (no Resolve required)."""

from __future__ import annotations

import pytest

from dvr import schema


def test_clip_properties_static() -> None:
    catalog = schema.get_topic("clip-properties")
    assert "Pan" in catalog
    assert "DynamicZoomEase" in catalog
    assert catalog["CompositeMode"]["type"] == "enum"
    assert "Difference" in catalog["CompositeMode"]["values"]
    assert catalog["CompositeMode"]["constants"]["Difference"] == 3
    assert catalog["ResizeFilter"]["constants"]["Linear"] == 15


def test_clip_property_aliases_and_coercion() -> None:
    assert schema.normalize_clip_property_key("crop_top") == "CropTop"
    assert schema.normalize_clip_properties({"zoom": "1.25", "blend": "multiply"}) == {
        "ZoomX": 1.25,
        "ZoomY": 1.25,
        "CompositeMode": 4,
    }


def test_clip_property_capabilities_static() -> None:
    caps = schema.get_topic("clip-capabilities")
    assert caps["static_properties"]["supported"] is True
    assert caps["transitions"]["supported"] is False
    assert caps["keyframe_animation"]["supported"] is False


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
