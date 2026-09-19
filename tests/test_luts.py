"""LUT and DCTL file management.

Everything runs against a temporary LUT root via ``DVR_LUT_DIR`` so the real
Resolve LUT directory is never touched.
"""

from __future__ import annotations

import pytest

from dvr import errors, luts


@pytest.fixture(autouse=True)
def lut_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DVR_LUT_DIR", str(tmp_path))
    return tmp_path


# --- path containment ------------------------------------------------------


@pytest.mark.parametrize("path", ["../escape.cube", "../../escape.cube", "/etc/escape.cube"])
def test_paths_cannot_escape_the_lut_root(path):
    with pytest.raises(errors.DvrError, match="outside the LUT directory"):
        luts.resolve_path(path)


def test_missing_file_is_reported(lut_root):
    with pytest.raises(errors.DvrError, match="No such file"):
        luts.resolve_path("nope.cube", must_exist=True)


# --- cube generation -------------------------------------------------------


def test_generate_cube_writes_a_well_formed_lut(lut_root):
    info = luts.generate_cube("dvr/identity.cube", "(r, g, b)", size=5, title="Identity")
    assert info["size"] == 5
    assert info["points"] == 125

    text = (lut_root / "dvr" / "identity.cube").read_text()
    lines = text.strip().splitlines()
    assert lines[0] == 'TITLE "Identity"'
    assert lines[1] == "LUT_3D_SIZE 5"
    # header (2) + blank separator (1) + one line per lattice point
    assert len(lines) == 3 + 125
    # .cube iterates red fastest: the second data row moves red only.
    assert lines[3] == "0.000000 0.000000 0.000000"
    assert lines[4].split()[1:] == ["0.000000", "0.000000"]
    assert float(lines[4].split()[0]) == pytest.approx(0.25)


def test_generate_cube_clamps_out_of_range_output(lut_root):
    luts.generate_cube("hot.cube", "(r * 10, g - 5, b)", size=2)
    values = [
        float(v)
        for line in (lut_root / "hot.cube").read_text().splitlines()[3:]
        for v in line.split()
    ]
    assert all(0.0 <= v <= 1.0 for v in values)


def test_generate_cube_refuses_to_overwrite_by_default():
    luts.generate_cube("a.cube", "(r, g, b)", size=2)
    with pytest.raises(errors.DvrError, match="already exists"):
        luts.generate_cube("a.cube", "(r, g, b)", size=2)
    luts.generate_cube("a.cube", "(r, g, b)", size=2, overwrite=True)


@pytest.mark.parametrize("size", [1, 0, 130])
def test_generate_cube_rejects_absurd_sizes(size):
    with pytest.raises(errors.DvrError, match="Cube size"):
        luts.generate_cube("x.cube", "(r, g, b)", size=size)


def test_generate_cube_requires_a_cube_suffix():
    with pytest.raises(errors.DvrError, match=r"must end in \.cube"):
        luts.generate_cube("x.3dl", "(r, g, b)", size=2)


@pytest.mark.parametrize("transform", ["(r, g)", "'nope'", "42"])
def test_transform_must_return_a_triplet(transform):
    with pytest.raises(errors.DvrError, match=r"must return an \(r, g, b\) tuple"):
        luts.generate_cube("x.cube", transform, size=2)


def test_transform_runs_in_the_sandbox():
    with pytest.raises(errors.DvrError, match="Dunder attribute access"):
        luts.generate_cube("x.cube", "__import__('os').getcwd()", size=2)


# --- listing ---------------------------------------------------------------


def test_listing_separates_luts_from_dctls(lut_root):
    luts.generate_cube("show/look.cube", "(r, g, b)", size=2)
    luts.write_dctl("show/tool.dctl", _DCTL)

    assert [f["path"] for f in luts.list_luts()] == ["show/look.cube"]
    assert [f["path"] for f in luts.list_dctls()] == ["show/tool.dctl"]
    assert luts.list_luts("show")[0]["name"] == "look.cube"


def test_listing_a_missing_subdir_is_empty():
    assert luts.list_luts("nothing-here") == []


# --- dctl ------------------------------------------------------------------


_DCTL = """__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y,
                            float p_R, float p_G, float p_B)
{
    return make_float3(p_R, p_G, p_B);
}
"""


def test_write_then_read_dctl_round_trips():
    luts.write_dctl("a.dctl", _DCTL)
    assert luts.read_dctl("a.dctl") == _DCTL


def test_dctl_requires_the_transform_entry_point():
    with pytest.raises(errors.DvrError, match="no `__DEVICE__ float3 transform"):
        luts.write_dctl("a.dctl", "float3 nope() { return 0; }")


def test_dctl_rejects_empty_source():
    with pytest.raises(errors.DvrError, match="empty"):
        luts.write_dctl("a.dctl", "   ")


@pytest.mark.parametrize(
    "broken",
    [
        _DCTL.replace("}", "", 1),
        # Unbalance the body, not the signature, so the entry-point check passes
        # first and the bracket check is what actually fires.
        _DCTL.replace("make_float3(", "make_float3", 1),
    ],
)
def test_dctl_rejects_unbalanced_brackets(broken):
    with pytest.raises(errors.DvrError, match="unbalanced"):
        luts.write_dctl("a.dctl", broken)


def test_entry_point_is_checked_before_brackets():
    """A file with neither must name the missing entry point, the bigger problem."""
    with pytest.raises(errors.DvrError, match="entry point"):
        luts.write_dctl("a.dctl", "int main( { return 0; }")


def test_invalid_dctl_never_lands_on_disk(lut_root):
    with pytest.raises(errors.DvrError):
        luts.write_dctl("a.dctl", "not a dctl")
    assert not (lut_root / "a.dctl").exists()


def test_dctl_requires_a_dctl_suffix():
    with pytest.raises(errors.DvrError, match=r"must end in \.dctl"):
        luts.write_dctl("a.txt", _DCTL)


# --- delete ----------------------------------------------------------------


def test_delete_removes_the_file(lut_root):
    luts.generate_cube("a.cube", "(r, g, b)", size=2)
    assert luts.delete_file("a.cube") == {"deleted": "a.cube"}
    assert not (lut_root / "a.cube").exists()


def test_delete_refuses_unrelated_files(lut_root):
    (lut_root / "notes.txt").write_text("keep me")
    with pytest.raises(errors.DvrError, match="not a LUT or DCTL"):
        luts.delete_file("notes.txt")
    assert (lut_root / "notes.txt").exists()


# --- path shape ------------------------------------------------------------
#
# Returned paths are handed to agents and fed straight back into the write and
# delete calls, so they must look identical on every platform. Windows would
# otherwise report "show\look.cube" where macOS reports "show/look.cube".


def test_returned_paths_always_use_forward_slashes():
    listed = luts.generate_cube("show/day/look.cube", "(r, g, b)", size=2)
    written = luts.write_dctl("show/day/tool.dctl", _DCTL)
    assert listed["path"] == "show/day/look.cube"
    assert written["path"] == "show/day/tool.dctl"
    assert [f["path"] for f in luts.list_luts()] == ["show/day/look.cube"]
    assert [f["path"] for f in luts.list_dctls()] == ["show/day/tool.dctl"]
    assert "\\" not in listed["path"]


def test_returned_paths_round_trip():
    """Whatever a listing reports must be usable as an argument."""
    luts.generate_cube("show/day/look.cube", "(r, g, b)", size=2)
    reported = luts.list_luts()[0]["path"]
    assert luts.resolve_path(reported, must_exist=True).is_file()
    assert luts.delete_file(reported) == {"deleted": reported}


def test_subdir_listing_reports_paths_from_the_root():
    luts.generate_cube("show/day/look.cube", "(r, g, b)", size=2)
    assert luts.list_luts("show")[0]["path"] == "show/day/look.cube"
