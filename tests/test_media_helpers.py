"""Tests for shared media helpers: filesystem scan and bin-path resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dvr import errors
from dvr.media import MediaPool, media_kind_for_path, scan_media_files


def test_media_kind_for_path() -> None:
    assert media_kind_for_path("a/b/clip.MOV") == "video"
    assert media_kind_for_path("music.wav") == "audio"
    assert media_kind_for_path("notes.txt") == "other"


def test_scan_media_files_skips_hidden_and_sidecar_files(tmp_path: Path) -> None:
    (tmp_path / "clip.mov").write_bytes(b"x")
    (tmp_path / "._clip.mov").write_bytes(b"x")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    sub = tmp_path / "audio"
    sub.mkdir()
    (sub / "mix.wav").write_bytes(b"xy")

    files = scan_media_files(tmp_path)
    names = [f["name"] for f in files]
    assert names == ["mix.wav", "clip.mov"] or sorted(names) == ["clip.mov", "mix.wav"]
    kinds = {f["name"]: f["kind"] for f in files}
    assert kinds == {"clip.mov": "video", "mix.wav": "audio"}
    by_name = {f["name"]: f for f in files}
    assert by_name["mix.wav"]["relative_path"] == str(Path("audio") / "mix.wav")
    assert by_name["mix.wav"]["size"] == 2


def test_scan_media_files_non_recursive(tmp_path: Path) -> None:
    (tmp_path / "clip.mov").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.mov").write_bytes(b"x")

    files = scan_media_files(tmp_path, recursive=False)
    assert [f["name"] for f in files] == ["clip.mov"]


def test_scan_media_files_missing_path_raises() -> None:
    with pytest.raises(errors.MediaError):
        scan_media_files("/definitely/not/a/real/path")


class _Folder:
    def __init__(self, name: str, *, subfolders: list[_Folder] | None = None) -> None:
        self.name = name
        self.subfolders = subfolders or []
        self.clips: list[Any] = []


class _Media:
    """Duck-typed MediaPool stand-in for the path helpers."""

    def __init__(self) -> None:
        self.root = _Folder("Root", subfolders=[_Folder("A", subfolders=[_Folder("B")])])

    def _find_folder(self, name: str) -> _Folder:
        for folder in self.walk():
            if folder.name == name:
                return folder
        raise errors.MediaError(f"No folder named {name!r}.")

    def ensure_folder(self, name: str, *, parent: _Folder) -> _Folder:
        for sub in parent.subfolders:
            if sub.name == name:
                return sub
        created = _Folder(name)
        parent.subfolders.append(created)
        return created

    def walk(self) -> list[_Folder]:
        out: list[_Folder] = []

        def visit(folder: _Folder) -> None:
            out.append(folder)
            for child in folder.subfolders:
                visit(child)

        visit(self.root)
        return out


def test_find_folder_path_accepts_slash_paths_and_names() -> None:
    media = _Media()
    assert MediaPool.find_folder_path(media, "A/B").name == "B"  # type: ignore[arg-type]
    assert MediaPool.find_folder_path(media, "B").name == "B"  # type: ignore[arg-type]
    assert MediaPool.find_folder_path(media, ["A", "B"]).name == "B"  # type: ignore[arg-type]
    assert MediaPool.find_folder_path(media, "").name == "Root"  # type: ignore[arg-type]


def test_find_folder_path_missing_segment_raises() -> None:
    media = _Media()
    with pytest.raises(errors.MediaError) as exc:
        MediaPool.find_folder_path(media, "A/Missing")  # type: ignore[arg-type]
    assert exc.value.state["missing"] == "Missing"


def test_ensure_folder_path_creates_each_segment() -> None:
    media = _Media()
    created = MediaPool.ensure_folder_path(media, "A/New/Deeper")  # type: ignore[arg-type]
    assert created.name == "Deeper"
    # Idempotent: a second call returns the same folder object.
    again = MediaPool.ensure_folder_path(media, "A/New/Deeper")  # type: ignore[arg-type]
    assert again is created
