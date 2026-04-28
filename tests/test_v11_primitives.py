"""Tests for the v1.1 primitives:

- ``MediaPool.find_or_import`` — dedup-by-path import helper.
- ``RenderNamespace.submit_and_wait`` — submit + wait, returning the
  rendered output path.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from dvr import errors
from dvr.media import MediaPool, _normalise_path
from tests.conftest import MockNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(name: str, *, path: str | None = None) -> MockNode:
    actual_path = path if path is not None else f"/x/{name}.mov"
    return MockNode(
        f"Clip({name})",
        {
            "GetName": name,
            "GetClipProperty": lambda key=None: (
                {"Clip Name": name, "File Path": actual_path}
                if key is None
                else {"Clip Name": name, "File Path": actual_path}.get(key, "")
            ),
            "SetClipProperty": True,
        },
    )


def _make_pool(*, clips: list[MockNode], import_returns: list[MockNode] | None = None):
    """Wire a MediaPool with a single root folder containing ``clips``."""
    root = MockNode(
        "Root",
        {
            "GetName": "Master",
            "GetClipList": list(clips),
            "GetSubFolderList": [],
            "SetClipProperty": True,
        },
    )
    pool_raw = MockNode(
        "MP",
        {
            "GetRootFolder": root,
            "GetCurrentFolder": root,
            "ImportMedia": lambda paths: list(import_returns or []),
            "SetCurrentFolder": True,
        },
    )
    project_raw = MockNode("Proj", {"GetMediaStorage": MockNode("Storage")})
    return MediaPool(pool_raw, project_raw), pool_raw, root


# ---------------------------------------------------------------------------
# MediaPool.find_or_import
# ---------------------------------------------------------------------------


def test_find_or_import_returns_existing_clip_without_import():
    existing = _make_clip("hero", path="/footage/hero.mov")
    pool, pool_raw, _root = _make_pool(clips=[existing])

    result = pool.find_or_import("/footage/hero.mov")

    assert result.name == "hero"
    # No ImportMedia call when the clip is already in the pool.
    assert not any(c[0] == "ImportMedia" for c in pool_raw.calls)


def test_find_or_import_imports_when_missing():
    new_clip = _make_clip("fresh", path="/footage/fresh.mov")
    pool, pool_raw, _root = _make_pool(clips=[], import_returns=[new_clip])

    result = pool.find_or_import("/footage/fresh.mov")

    assert result.name == "fresh"
    import_calls = [c for c in pool_raw.calls if c[0] == "ImportMedia"]
    assert len(import_calls) == 1
    assert import_calls[0][1][0] == ["/footage/fresh.mov"]


def test_find_or_import_normalises_paths():
    """Paths with trailing separators / redundant segments still match."""
    existing = _make_clip("seq", path="/data/seq.mov")
    pool, pool_raw, _root = _make_pool(clips=[existing])

    result = pool.find_or_import("/data/./seq.mov")

    assert result.name == "seq"
    assert not any(c[0] == "ImportMedia" for c in pool_raw.calls)


def test_find_or_import_routes_through_import_to_when_folder_given():
    new_clip = _make_clip("bin_clip", path="/bin/clip.mov")
    new_folder = MockNode(
        "Bin",
        {
            "GetName": "shots",
            "GetClipList": [],
            "GetSubFolderList": [],
            "SetClipProperty": True,
        },
    )
    root = MockNode(
        "Root",
        {
            "GetName": "Master",
            "GetClipList": [],
            "GetSubFolderList": [],
            "AddSubFolder": new_folder,
            "SetClipProperty": True,
        },
    )
    pool_raw = MockNode(
        "MP",
        {
            "GetRootFolder": root,
            "GetCurrentFolder": root,
            "AddSubFolder": new_folder,
            "SetCurrentFolder": True,
            "ImportMedia": [new_clip],
        },
    )
    project_raw = MockNode("Proj", {"GetMediaStorage": MockNode("Storage")})
    pool = MediaPool(pool_raw, project_raw)

    result = pool.find_or_import("/bin/clip.mov", folder="shots")

    assert result.name == "bin_clip"
    # AddSubFolder fired because "shots" didn't exist (create_missing default).
    assert any(c[0] == "AddSubFolder" for c in pool_raw.calls)


def test_find_or_import_raises_when_import_returns_empty():
    pool, _pool_raw, _root = _make_pool(clips=[], import_returns=[])

    with pytest.raises(errors.MediaImportError) as ctx:
        pool.find_or_import("/missing/file.mov")

    # MediaPool.import_media itself raises a clear error first; verify the
    # underlying error path fires (we don't need our own wrapper to take over).
    assert "no items" in ctx.value.message.lower() or "no clips" in ctx.value.message.lower()


def test_normalise_path_collapses_dot_segments():
    assert _normalise_path("/a/b/./c") == _normalise_path("/a/b/c")
    assert _normalise_path("/a/b/../c") == _normalise_path("/a/c")


# ---------------------------------------------------------------------------
# RenderNamespace.submit_and_wait
# ---------------------------------------------------------------------------


def _make_render_namespace(
    *,
    output_filename: str = "/tmp/out/render_v001.mov",
    job_status_sequence: list[str] | None = None,
):
    """Build a RenderNamespace wired against a mock project + Resolve.

    ``job_status_sequence`` lets a test drive the wait() loop through a
    sequence of statuses (default: just ``["Complete"]``).
    """
    from dvr.render import RenderNamespace
    from dvr.resolve import Resolve

    statuses = list(job_status_sequence or ["Complete"])
    queue: list[dict] = []
    job_counter = {"n": 0}

    def add_render_job(*_a, **_kw):
        job_counter["n"] += 1
        jid = f"job{job_counter['n']}"
        queue.append({"JobId": jid, "OutputFilename": output_filename})
        return jid

    def get_status(_jid):
        # Pop a status each call; sticky on the last entry.
        if len(statuses) > 1:
            return {"JobStatus": statuses.pop(0), "CompletionPercentage": 0}
        return {"JobStatus": statuses[0], "CompletionPercentage": 100}

    project_raw = MockNode(
        "Proj",
        {
            "GetCurrentRenderFormatAndCodec": {"format": "mov", "codec": "ProRes4444XQ"},
            "GetRenderJobList": lambda: list(queue),
            "GetRenderFormats": {},
            "GetRenderCodecs": lambda fmt: {},
            "GetRenderPresetList": [],
            "SetCurrentRenderFormatAndCodec": None,
            "SetRenderSettings": True,
            "AddRenderJob": add_render_job,
            "StartRendering": True,
            "GetRenderJobStatus": get_status,
            "GetCurrentTimeline": MockNode("TL", {"GetName": "TL"}),
            "IsRenderingInProgress": False,
        },
    )
    pm = MockNode(
        "PM",
        {
            "GetCurrentProject": project_raw,
            "SaveProject": True,
        },
    )
    raw_resolve = MockNode(
        "Resolve",
        {
            "GetProjectManager": pm,
            "GetProduct": "DaVinci Resolve Studio",
            "GetVersionString": "20.3-mock",
            "OpenPage": True,
        },
    )
    r = Resolve.__new__(Resolve)
    r._raw = raw_resolve  # type: ignore[attr-defined]
    r._project_manager = pm  # type: ignore[attr-defined]
    r._socket_path = None  # type: ignore[attr-defined]

    return RenderNamespace(r), project_raw, queue


def test_submit_and_wait_returns_output_path():
    ns, project_raw, _queue = _make_render_namespace(
        output_filename="/tmp/out/hero_v001.mov",
    )

    # Speed up the wait() poll loop.
    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        path = ns.submit_and_wait(
            target_dir="/tmp/out",
            custom_name="hero_v001",
            format="mov",
            codec="ProRes4444XQ",
        )

    assert path == "/tmp/out/hero_v001.mov"
    # Verify both the queue mutation and the start fired.
    assert any(c[0] == "AddRenderJob" for c in project_raw.calls)
    assert any(c[0] == "StartRendering" for c in project_raw.calls)


def test_submit_and_wait_polls_until_complete():
    ns, _project_raw, _queue = _make_render_namespace(
        output_filename="/out/x.mov",
        job_status_sequence=["Rendering", "Rendering", "Complete"],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None):
        path = ns.submit_and_wait(target_dir="/out", custom_name="x")

    assert path == "/out/x.mov"


def test_submit_and_wait_raises_on_failed_status():
    ns, _project_raw, _queue = _make_render_namespace(
        output_filename="/out/x.mov",
        job_status_sequence=["Failed"],
    )

    with patch.object(time, "sleep", lambda *_a, **_kw: None), pytest.raises(errors.RenderJobError):
        ns.submit_and_wait(target_dir="/out", custom_name="x")


def test_submit_and_wait_raises_when_output_path_missing():
    """If Resolve evicts the job from the queue immediately on completion,
    OutputFilename is unreachable. Surface a clear error rather than ``""``.
    """
    ns, _project_raw, _queue = _make_render_namespace(
        output_filename="",  # simulate Resolve dropping the OutputFilename
    )

    with (
        patch.object(time, "sleep", lambda *_a, **_kw: None),
        pytest.raises(errors.RenderError) as ctx,
    ):
        ns.submit_and_wait(target_dir="/out", custom_name="ghost")

    assert "no output path" in ctx.value.message.lower()
