"""Tests for ACES color presets, IDT/ODT setters, project presets, and IMF ingest."""

from __future__ import annotations

import pytest

from dvr import errors
from dvr.media import MediaPool
from dvr.project import Project, ProjectNamespace, _looks_like_hdr_pq_aces
from dvr.spec import COLOR_PRESETS, SETTINGS_ORDER

# ---------------------------------------------------------------------------
# COLOR_PRESETS — ACES entries exist and have ACES color science.
# ---------------------------------------------------------------------------


def test_aces_color_presets_present() -> None:
    expected = {
        "aces_p3d65_pq_4000",
        "aces_p3d65_pq_1000",
        "aces_rec2020_pq_4000",
        "aces_rec2020_pq_1000",
        "aces_rec709",
    }
    assert expected.issubset(COLOR_PRESETS.keys())


def test_aces_presets_use_acescct_color_science() -> None:
    for name, preset in COLOR_PRESETS.items():
        if not name.startswith("aces_"):
            continue
        assert preset["colorScienceMode"] == "acescct", (
            f"{name} should set colorScienceMode = acescct"
        )
        assert preset["colorAcesNodeLUTProcessingSpace"] == "acesccAp1"


def test_settings_order_includes_aces_keys() -> None:
    # ACES keys must be in the ordering so spec.apply applies them after
    # colorScienceMode is set.
    assert "colorAcesIDT" in SETTINGS_ORDER
    assert "colorAcesODT" in SETTINGS_ORDER
    assert "colorAcesNodeLUTProcessingSpace" in SETTINGS_ORDER
    # And they must come after colorScienceMode.
    assert SETTINGS_ORDER.index("colorAcesIDT") > SETTINGS_ORDER.index("colorScienceMode")


# ---------------------------------------------------------------------------
# HDR PQ ACES detection heuristic.
# ---------------------------------------------------------------------------


def test_looks_like_hdr_pq_aces_recognizes_ui_labels() -> None:
    assert _looks_like_hdr_pq_aces("P3-D65 ST2084 (4000 nits)")
    assert _looks_like_hdr_pq_aces("Rec.2020 ST2084 (1000 nits)")
    assert _looks_like_hdr_pq_aces("Rec.2020 PQ")


def test_looks_like_hdr_pq_aces_recognizes_amf_ids() -> None:
    assert _looks_like_hdr_pq_aces("InvRRTODT.Academy.P3D65_4000nits_15nits_ST2084.a1.1.0")
    assert _looks_like_hdr_pq_aces("RRTODT.Academy.Rec2020_1000nits_15nits_ST2084.a1.1.0")
    assert _looks_like_hdr_pq_aces("InvOutput.Academy.P3-D65_4000nit_in_P3-D65_ST2084.a2.v1")
    assert _looks_like_hdr_pq_aces("Output.Academy.P3-D65_1000nit_in_Rec2100-D65_ST2084.a2.v1")


def test_looks_like_hdr_pq_aces_rejects_basic_names() -> None:
    assert not _looks_like_hdr_pq_aces("No Input Transform")
    assert not _looks_like_hdr_pq_aces("Rec.709")
    assert not _looks_like_hdr_pq_aces("P3-D65")
    assert not _looks_like_hdr_pq_aces("Rec.2020")
    assert not _looks_like_hdr_pq_aces("DCDM")


# ---------------------------------------------------------------------------
# Project.set_setting — special-cased error for HDR PQ ACES rejection.
# ---------------------------------------------------------------------------


def test_set_setting_hdr_pq_idt_raises_with_ui_hint(mock_resolve) -> None:
    mock_resolve.project.responses["SetSetting"] = lambda *a, **k: False
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    with pytest.raises(errors.SettingsError) as ctx:
        project.set_setting("colorAcesIDT", "P3-D65 ST2084 (4000 nits)")
    err = ctx.value
    assert "Resolve rejected" in err.message
    assert err.fix and "Project Settings" in err.fix
    assert "set_preset" in (err.fix or "")


def test_set_setting_basic_idt_uses_generic_error(mock_resolve) -> None:
    mock_resolve.project.responses["SetSetting"] = lambda *a, **k: False
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    with pytest.raises(errors.SettingsError) as ctx:
        project.set_setting("colorAcesIDT", "Rec.709")
    err = ctx.value
    # Falls through to the generic message for non-HDR-PQ values.
    assert "Could not set project setting" in err.message


def test_set_aces_idt_calls_set_setting(mock_resolve) -> None:
    mock_resolve.project.responses["SetSetting"] = lambda *a, **k: True
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    project.set_aces_idt("Rec.709")
    keys = [c[1][0] for c in mock_resolve.project.calls if c[0] == "SetSetting"]
    assert "colorAcesIDT" in keys


def test_set_aces_odt_calls_set_setting(mock_resolve) -> None:
    mock_resolve.project.responses["SetSetting"] = lambda *a, **k: True
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    project.set_aces_odt("No Output Transform")
    keys = [c[1][0] for c in mock_resolve.project.calls if c[0] == "SetSetting"]
    assert "colorAcesODT" in keys


# ---------------------------------------------------------------------------
# Project preset wrappers.
# ---------------------------------------------------------------------------


def test_presets_returns_list_of_dicts(mock_resolve) -> None:
    mock_resolve.project.responses["GetPresetList"] = [
        {"Name": "Current Project", "Width": 3840, "Height": 2160},
        {"Name": "System Config", "Width": 1920, "Height": 1080},
    ]
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    presets = project.presets()
    assert len(presets) == 2
    assert presets[0]["Name"] == "Current Project"


def test_set_preset_calls_resolve_api(mock_resolve) -> None:
    mock_resolve.project.responses["SetPreset"] = lambda name: True
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    project.set_preset("MyPreset")
    calls = [c for c in mock_resolve.project.calls if c[0] == "SetPreset"]
    assert len(calls) == 1
    assert calls[0][1] == ("MyPreset",)


def test_set_preset_failure_raises_with_available_list(mock_resolve) -> None:
    mock_resolve.project.responses["SetPreset"] = lambda name: False
    mock_resolve.project.responses["GetPresetList"] = [
        {"Name": "Current Project"},
        {"Name": "System Config"},
    ]
    project = Project(mock_resolve.project, mock_resolve.project_manager)
    with pytest.raises(errors.ProjectError) as ctx:
        project.set_preset("Missing")
    err = ctx.value
    assert "Missing" in err.message
    assert err.state and err.state.get("available") == ["Current Project", "System Config"]


# ---------------------------------------------------------------------------
# ProjectNamespace.load — distinguish "doesn't exist" from "won't load".
# ---------------------------------------------------------------------------


def test_load_existing_but_locked_says_unsaved_state(mock_resolve) -> None:
    # Simulate: GetCurrentProject returns a project named "Other"; LoadProject returns None;
    # the requested name IS in the folder listing.
    other = mock_resolve.project_manager.responses["GetCurrentProject"]
    other.responses["GetName"] = "Other"
    mock_resolve.project_manager.responses["LoadProject"] = lambda name: None
    mock_resolve.project_manager.responses["GetProjectListInCurrentFolder"] = ["Target", "Other"]
    ns = ProjectNamespace(mock_resolve, mock_resolve.project_manager)
    with pytest.raises(errors.ProjectError) as ctx:
        ns.load("Target")
    err = ctx.value
    assert "refused to load existing project" in err.message
    assert err.cause and "unsaved" in err.cause.lower()
    assert err.fix and "save or close" in err.fix.lower()
    assert err.state and err.state.get("current_project") == "Other"


def test_load_nonexistent_keeps_original_message(mock_resolve) -> None:
    mock_resolve.project_manager.responses["LoadProject"] = lambda name: None
    mock_resolve.project_manager.responses["GetProjectListInCurrentFolder"] = ["Other"]
    ns = ProjectNamespace(mock_resolve, mock_resolve.project_manager)
    with pytest.raises(errors.ProjectError) as ctx:
        ns.load("NotThere")
    err = ctx.value
    assert "Could not load project" in err.message
    assert err.cause and "does not exist" in err.cause


# ---------------------------------------------------------------------------
# MediaPool.import_imf
# ---------------------------------------------------------------------------


def test_import_imf_validates_directory(tmp_path, mock_resolve) -> None:
    pool = MediaPool(mock_resolve.project.responses["GetMediaPool"], mock_resolve.project)
    not_a_dir = tmp_path / "missing"
    with pytest.raises(errors.MediaImportError) as ctx:
        pool.import_imf(str(not_a_dir))
    assert "not a directory" in ctx.value.message


def test_import_imf_requires_cpl(tmp_path, mock_resolve) -> None:
    folder = tmp_path / "imf_no_cpl"
    folder.mkdir()
    (folder / "ASSETMAP.xml").write_text("<x/>", encoding="utf-8")
    pool = MediaPool(mock_resolve.project.responses["GetMediaPool"], mock_resolve.project)
    with pytest.raises(errors.MediaImportError) as ctx:
        pool.import_imf(str(folder))
    assert "CPL" in ctx.value.message


def test_import_imf_uses_media_storage(tmp_path, mock_resolve) -> None:
    folder = tmp_path / "imf"
    folder.mkdir()
    (folder / "CPL_test.xml").write_text("<x/>", encoding="utf-8")

    # Wire MediaStorage onto the project mock.
    from tests.conftest import MockNode

    storage = MockNode(
        "MediaStorage",
        {"AddItemListToMediaPool": lambda items: [MockNode("Clip", {"GetName": "PIC"})]},
    )
    mock_resolve.project.responses["GetMediaStorage"] = storage

    pool = MediaPool(mock_resolve.project.responses["GetMediaPool"], mock_resolve.project)
    clips = pool.import_imf(str(folder))

    assert len(clips) == 1
    # Storage should have been called with the IMF folder path.
    storage_calls = [c for c in storage.calls if c[0] == "AddItemListToMediaPool"]
    assert len(storage_calls) == 1
    assert storage_calls[0][1] == ([str(folder)],)
