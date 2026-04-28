"""Media pool, clips (media-pool items), folders, and media storage.

Three things this module covers:

* :class:`MediaStorage` — Resolve's view of the filesystem (mounted
  volumes, sub-folders, files). Used for bulk imports and "reveal in
  finder" style operations.
* :class:`MediaPool` — the project-scoped clip database, organized into
  folders (a.k.a. bins). Imports media, creates timelines, links proxies,
  performs auto-sync.
* :class:`Clip` (a.k.a. ``MediaPoolItem``) — a single clip in a folder,
  with its metadata, markers, flags, audio mapping, and proxy links.

Naming notes
------------

A *clip* in this module is what Resolve internally calls
``MediaPoolItem`` — the asset sitting in a bin, before it's placed on a
timeline. The thing on a timeline is :class:`dvr.timeline.TimelineItem`.
Older releases called this class ``Asset``; that name remains as a
deprecated alias.

A *folder* is the bin in the media pool. Older releases called it
``Bin``; that name remains as a deprecated alias.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, List  # noqa: UP035 — `List` avoids `list` shadow

from . import errors
from ._wrap import require

if TYPE_CHECKING:
    from .timeline import Timeline

logger = logging.getLogger("dvr.media")


# ---------------------------------------------------------------------------
# Clip (the thing in the pool — Resolve's MediaPoolItem)
# ---------------------------------------------------------------------------


class Clip:
    """A clip in the media pool (Resolve's ``MediaPoolItem``).

    For the *placed instance* of a clip on a timeline, see
    :class:`dvr.timeline.TimelineItem` instead.
    """

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def name(self) -> str:
        return self._raw.GetName()

    @name.setter
    def name(self, value: str) -> None:
        if not self._raw.SetClipProperty("Clip Name", value):
            raise errors.MediaError(
                f"Could not rename clip to {value!r}.",
                cause="SetClipProperty('Clip Name', ...) returned False.",
                state={"current": self.name, "requested": value},
            )

    @property
    def duration(self) -> str:
        """Duration as a timecode string."""
        return self._raw.GetClipProperty("Duration") or ""

    @property
    def file_path(self) -> str:
        return self._raw.GetClipProperty("File Path") or ""

    # `path` is the canonical short name; `file_path` keeps the older
    # explicit name for callers that prefer it.
    @property
    def path(self) -> str:
        return self.file_path

    @property
    def fps(self) -> float:
        try:
            return float(self._raw.GetClipProperty("FPS") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def resolution(self) -> str:
        """e.g. ``3840x2160``."""
        return self._raw.GetClipProperty("Resolution") or ""

    @property
    def codec(self) -> str:
        """Video codec as reported by Resolve (e.g. ``Apple ProRes 4444 XQ``)."""
        return str(self._raw.GetClipProperty("Video Codec") or "")

    @property
    def audio_codec(self) -> str:
        return str(self._raw.GetClipProperty("Audio Codec") or "")

    @property
    def kind(self) -> str:
        """Clip type — e.g. ``Video``, ``Audio``, ``Compound Clip``, ``Generator``."""
        return str(self._raw.GetClipProperty("Type") or "")

    # --- generic property surface ---------------------------------------

    def get_property(self, key: str | None = None) -> Any:
        return self._raw.GetClipProperty(key) if key else self._raw.GetClipProperty()

    def set_property(self, key: str, value: Any, *, raise_on_failure: bool = True) -> bool:
        """Set a media-pool clip property. Returns True on success.

        With ``raise_on_failure=True`` (default), raises :class:`MediaError`
        on failure. With ``raise_on_failure=False``, returns False so you
        can do batch counting like ``sum(1 for c in clips if c.set_property(...,
        raise_on_failure=False))``.
        """
        ok = bool(self._raw.SetClipProperty(key, value))
        if not ok and raise_on_failure:
            current = self._raw.GetClipProperty(key)
            raise errors.MediaError(
                f"Could not set clip property {key!r} to {value!r}.",
                cause="SetClipProperty returned False.",
                fix="See `dvr schema clip-properties` for valid keys per Resolve version.",
                state={"clip": self.name, "key": key, "value": value, "current": current},
            )
        return ok

    # --- metadata -------------------------------------------------------

    def get_metadata(self, key: str | None = None) -> Any:
        return self._raw.GetMetadata(key) if key else self._raw.GetMetadata()

    def set_metadata(self, key_or_dict: str | dict[str, Any], value: Any = None) -> None:
        if isinstance(key_or_dict, dict):
            ok = self._raw.SetMetadata(key_or_dict)
        else:
            ok = self._raw.SetMetadata(key_or_dict, value)
        if not ok:
            raise errors.MediaError(
                f"Could not set metadata on clip {self.name!r}.",
                cause="SetMetadata returned False — invalid key or unsupported value type.",
                state={"clip": self.name},
            )

    # --- flags / colors / markers ---------------------------------------

    @property
    def color(self) -> str:
        return self._raw.GetClipColor() or ""

    @color.setter
    def color(self, value: str) -> None:
        if value:
            self._raw.SetClipColor(value)
        else:
            self._raw.ClearClipColor()

    def flags(self) -> list[str]:
        return list(self._raw.GetFlagList() or [])

    def add_flag(self, color: str) -> None:
        if not self._raw.AddFlag(color):
            raise errors.MediaError(
                f"Could not add {color!r} flag.",
                cause="AddFlag returned False — color may be invalid.",
                state={"clip": self.name, "color": color},
            )

    def clear_flags(self, color: str | None = None) -> None:
        self._raw.ClearFlags(color) if color else self._raw.ClearFlags()

    def markers(self) -> dict[int, dict[str, Any]]:
        return dict(self._raw.GetMarkers() or {})

    def add_marker(
        self,
        frame: int,
        *,
        color: str = "Blue",
        name: str = "",
        note: str = "",
        duration: int = 1,
        custom_data: str = "",
    ) -> None:
        if not self._raw.AddMarker(frame, color, name, note, duration, custom_data):
            raise errors.MediaError(
                f"Could not add marker on clip {self.name!r}.",
                cause="AddMarker returned False — frame may be out of range.",
                state={"clip": self.name, "frame": frame, "color": color},
            )

    def delete_markers(self, color: str | None = None) -> None:
        if color is not None:
            self._raw.DeleteMarkersByColor(color)
        else:
            self._raw.DeleteMarkersByColor("All")

    # --- in/out ---------------------------------------------------------

    def get_mark_in_out(self, media_type: str = "all") -> dict[str, dict[str, int]]:
        return dict(self._raw.GetMarkInOut() or {})

    def set_mark_in_out(self, in_frame: int, out_frame: int, *, media_type: str = "all") -> None:
        if not self._raw.SetMarkInOut(in_frame, out_frame, media_type):
            raise errors.MediaError(
                "Could not set mark in/out.",
                state={
                    "clip": self.name,
                    "in_frame": in_frame,
                    "out_frame": out_frame,
                    "media_type": media_type,
                },
            )

    def clear_mark_in_out(self, *, media_type: str = "all") -> None:
        self._raw.ClearMarkInOut(media_type)

    # --- proxy ----------------------------------------------------------

    def link_proxy(self, proxy_path: str) -> None:
        if not self._raw.LinkProxyMedia(proxy_path):
            raise errors.MediaError(
                f"Could not link proxy {proxy_path!r}.",
                cause="LinkProxyMedia returned False — file may be missing or wrong format.",
                state={"clip": self.name, "proxy_path": proxy_path},
            )

    def unlink_proxy(self) -> None:
        self._raw.UnlinkProxyMedia()

    def link_full_resolution(self) -> None:
        self._raw.LinkFullResolutionMedia()

    # --- replace / relink -----------------------------------------------

    def replace(self, source_path: str, *, preserve_subclip: bool = True) -> None:
        """Replace the underlying source file.

        ``preserve_subclip=True`` keeps trim/marks if the API exposes
        ``ReplaceClipPreserveSubClip`` (newer Resolve versions). Otherwise
        falls back to ``ReplaceClip`` which resets the clip extents.
        """
        ok = (
            self._raw.ReplaceClipPreserveSubClip(source_path)
            if preserve_subclip and hasattr(self._raw, "ReplaceClipPreserveSubClip")
            else self._raw.ReplaceClip(source_path)
        )
        if not ok:
            raise errors.MediaError(
                f"Could not replace clip {self.name!r} with {source_path!r}.",
                cause="ReplaceClip returned False — path may be missing or unreadable.",
                state={"clip": self.name, "source_path": source_path},
            )

    # --- transcribe -----------------------------------------------------

    def transcribe(self, language: str = "auto") -> None:
        """Run Resolve's Whisper-based audio transcription on this clip."""
        if not self._raw.TranscribeAudio(language):
            raise errors.MediaError(
                f"Could not transcribe clip {self.name!r}.",
                cause="TranscribeAudio returned False.",
                fix="Open Resolve's Edit page to verify Whisper models are downloaded.",
                state={"clip": self.name, "language": language},
            )

    def clear_transcription(self) -> None:
        self._raw.ClearTranscription()

    # --- inspection -----------------------------------------------------

    def inspect(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "duration": self.duration,
            "fps": self.fps,
            "resolution": self.resolution,
            "codec": self.codec,
            "kind": self.kind,
            "color": self.color,
            "flags": self.flags(),
            "marker_count": len(self.markers()),
        }


# Deprecated aliases — kept so existing user code continues to import.
# Prefer :class:`Clip` in new code.
Asset = Clip
MediaPoolItem = Clip


# ---------------------------------------------------------------------------
# Folder (a bin in the media pool)
# ---------------------------------------------------------------------------


class Folder:
    """A folder (bin) in the media pool (Resolve's ``Folder``).

    Older releases called this class ``Bin``; that name remains as a
    deprecated alias.
    """

    def __init__(self, raw: Any, pool: MediaPool) -> None:
        self._raw = raw
        self._pool = pool

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def name(self) -> str:
        return self._raw.GetName()

    @name.setter
    def name(self, value: str) -> None:
        self.rename(value)

    # --- contents (properties on Folder, not methods) -------------------

    @property
    def clips(self) -> list[Clip]:
        """Direct child clips (one level — does not recurse)."""
        return [Clip(c) for c in (self._raw.GetClipList() or [])]

    @property
    def subfolders(self) -> list[Folder]:
        """Direct child folders (one level — does not recurse)."""
        return [Folder(f, self._pool) for f in (self._raw.GetSubFolderList() or [])]

    # Legacy method-form aliases.
    def assets(self) -> list[Clip]:
        return self.clips

    def subbins(self) -> list[Folder]:
        return self.subfolders

    # --- recursion ------------------------------------------------------

    def walk(self) -> Iterator[Folder]:
        """Yield this folder and every (sub-)folder beneath it (depth-first).

        Replaces the recursive ``build_clip_lookup``-style helpers callers
        used to write themselves. Pair with :meth:`all_clips` for "every
        clip anywhere under here".
        """
        yield self
        stack: list[Folder] = list(self.subfolders)
        while stack:
            current = stack.pop()
            yield current
            stack.extend(current.subfolders)

    def all_clips(self) -> Iterator[Clip]:
        """Yield every clip in this folder and all descendants (depth-first)."""
        for folder in self.walk():
            yield from folder.clips

    def find_clip(
        self,
        *,
        name: str | None = None,
        predicate: Callable[[Clip], bool] | None = None,
    ) -> Clip | None:
        """Return the first descendant clip matching ``name`` or ``predicate``.

        Recurses through subfolders. Mutually exclusive: pass either
        ``name=`` (exact match on :attr:`Clip.name`) or ``predicate=``.
        """
        if (name is None) == (predicate is None):
            raise errors.MediaError(
                "Folder.find_clip requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda c: c.name == name)
        for clip in self.all_clips():
            if check(clip):
                return clip
        return None

    def find_clips(
        self,
        *,
        name: str | None = None,
        predicate: Callable[[Clip], bool] | None = None,
    ) -> list[Clip]:
        """Like :meth:`find_clip` but returns every match (possibly empty)."""
        if (name is None) == (predicate is None):
            raise errors.MediaError(
                "Folder.find_clips requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda c: c.name == name)
        return [clip for clip in self.all_clips() if check(clip)]

    # --- mutations on the folder itself ---------------------------------

    def add_subfolder(self, name: str) -> Folder:
        """Create (and return) a sub-folder by name."""
        return self._pool.add_folder(name, parent=self)

    def rename(self, name: str) -> None:
        """Rename this folder. Raises :class:`MediaError` on collision."""
        if not self._raw.SetClipProperty("Clip Name", name):
            raise errors.MediaError(
                f"Could not rename folder to {name!r}.",
                cause="SetClipProperty('Clip Name', ...) returned False.",
                fix="A sibling folder may already use this name.",
                state={"current": self.name, "requested": name},
            )

    def delete(self) -> None:
        """Delete this folder from the media pool. Removes all contents."""
        # Resolve's MediaPool.DeleteFolders takes a list of folder objects.
        if not self._pool.raw.DeleteFolders([self._raw]):
            raise errors.MediaError(
                f"Could not delete folder {self.name!r}.",
                cause="DeleteFolders returned False — folder may be the root or locked.",
                state={"folder": self.name},
            )

    def move(self, clips: Iterable[Clip], *, into: Folder | None = None) -> None:
        """Move clips into this folder (default), or into ``into`` if given."""
        target = into if into is not None else self
        self._pool.move(clips, target)

    # --- collaboration / export ----------------------------------------

    def is_stale(self) -> bool:
        """In collaboration mode, returns True if the folder needs refresh."""
        return bool(self._raw.GetIsFolderStale())

    def transcribe(self, language: str = "auto") -> None:
        """Bulk-transcribe every clip in the folder."""
        if not self._raw.TranscribeAudio(language):
            raise errors.MediaError(
                f"Could not transcribe folder {self.name!r}.",
                cause="Folder.TranscribeAudio returned False.",
                state={"folder": self.name, "language": language},
            )

    def export(self, file_path: str) -> None:
        """Export the folder as a ``.drb``."""
        if not self._raw.Export(file_path):
            raise errors.MediaError(
                f"Could not export folder {self.name!r}.",
                state={"folder": self.name, "file_path": file_path},
            )

    def inspect(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "clip_count": len(self.clips),
            "subfolder_count": len(self.subfolders),
        }


# Deprecated alias.
Bin = Folder


# ---------------------------------------------------------------------------
# MediaStorage (filesystem view)
# ---------------------------------------------------------------------------


class MediaStorage:
    """Resolve's view of the local/connected filesystem."""

    def __init__(self, raw: Any, pool: MediaPool) -> None:
        self._raw = raw
        self._pool = pool

    def volumes(self) -> List[str]:  # noqa: UP006
        return [str(v) for v in (self._raw.GetMountedVolumeList() or [])]

    def subfolders(self, path: str) -> List[str]:  # noqa: UP006
        return [str(p) for p in (self._raw.GetSubFolderList(path) or [])]

    def files(self, path: str) -> List[str]:  # noqa: UP006
        return [str(p) for p in (self._raw.GetFileList(path) or [])]

    def reveal(self, path: str) -> None:
        self._raw.RevealInStorage(path)

    def add_to_pool(
        self,
        items: Iterable[str | dict[str, Any]],
        *,
        folder: Folder | None = None,
    ) -> list[Clip]:
        """Import file paths (or ``{"FilePath": ..., "StartIndex": ...}`` dicts) into the pool."""
        if folder is not None:
            self._pool.set_current_folder(folder)
        item_list = list(items)
        result = self._raw.AddItemListToMediaPool(item_list)
        if not result:
            raise errors.MediaImportError(
                "Could not import any items from media storage.",
                cause="AddItemListToMediaPool returned no items.",
                fix="Verify the paths exist and are accessible to Resolve.",
                state={"requested_count": len(item_list)},
            )
        return [Clip(it) for it in result]


# ---------------------------------------------------------------------------
# MediaPool
# ---------------------------------------------------------------------------


class MediaPool:
    """The project-scoped media pool."""

    def __init__(self, raw: Any, resolve_raw: Any) -> None:
        self._raw = raw
        self._resolve = resolve_raw

    @property
    def raw(self) -> Any:
        return self._raw

    # --- folders --------------------------------------------------------

    @property
    def root(self) -> Folder:
        """The root folder of the media pool."""
        return Folder(self._raw.GetRootFolder(), self)

    @property
    def current_folder(self) -> Folder:
        """The folder currently selected in the UI."""
        raw = require(
            self._raw.GetCurrentFolder(),
            error=errors.MediaError,
            message="Could not determine the current folder.",
            cause="GetCurrentFolder returned None.",
        )
        return Folder(raw, self)

    # Legacy method-form alias for :attr:`current_folder`.
    def current_bin(self) -> Folder:
        return self.current_folder

    def set_current_folder(self, folder: Folder | str) -> Folder:
        target = folder if isinstance(folder, Folder) else self._find_folder(folder)
        if not self._raw.SetCurrentFolder(target.raw):
            raise errors.MediaError(
                f"Could not set current folder to {target.name!r}.",
                state={"requested": target.name},
            )
        return target

    # Legacy alias.
    def set_current_bin(self, bin: Folder | str) -> Folder:
        return self.set_current_folder(bin)

    def add_folder(self, name: str, *, parent: Folder | None = None) -> Folder:
        """Create a new (sub-)folder under ``parent`` (defaults to the current folder)."""
        parent_raw = parent.raw if parent else self._raw.GetCurrentFolder()
        raw = self._raw.AddSubFolder(parent_raw, name)
        if raw is None:
            raise errors.MediaError(
                f"Could not create folder {name!r}.",
                cause="AddSubFolder returned None — name may collide.",
                state={"name": name},
            )
        return Folder(raw, self)

    # Legacy alias.
    def add_subbin(self, name: str, *, parent: Folder | None = None) -> Folder:
        return self.add_folder(name, parent=parent)

    def ensure_folder(self, name: str, *, parent: Folder | None = None) -> Folder:
        """Get-or-create a (sub-)folder by name. Idempotent."""
        parent_folder = parent or self.root
        for sub in parent_folder.subfolders:
            if sub.name == name:
                return sub
        return self.add_folder(name, parent=parent_folder)

    # Legacy alias.
    def ensure_bin(self, name: str, *, parent: Folder | None = None) -> Folder:
        return self.ensure_folder(name, parent=parent)

    def _find_folder(self, name: str) -> Folder:
        # Depth-first search from root.
        for folder in self.walk():
            if folder.name == name:
                return folder
        raise errors.MediaError(
            f"No folder named {name!r}.",
            fix="Inspect available folders via `pool.root.subfolders`.",
            state={"requested": name},
        )

    def find_folder(self, name: str) -> Folder | None:
        """Return the first folder named ``name`` (depth-first), or ``None``."""
        for folder in self.walk():
            if folder.name == name:
                return folder
        return None

    def walk(self) -> Iterator[Folder]:
        """Yield every folder in the pool (depth-first from root)."""
        yield from self.root.walk()

    def find_clips(
        self,
        *,
        name: str | None = None,
        predicate: Callable[[Clip], bool] | None = None,
    ) -> list[Clip]:
        """Recursively search the entire pool for matching clips.

        Pass either ``name=`` (exact match) or ``predicate=`` (callable
        returning bool). Replaces ad-hoc ``build_clip_lookup`` helpers.
        """
        if (name is None) == (predicate is None):
            raise errors.MediaError(
                "MediaPool.find_clips requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda c: c.name == name)
        results: list[Clip] = []
        for folder in self.walk():
            results.extend(c for c in folder.clips if check(c))
        return results

    def find_clip(
        self,
        *,
        name: str | None = None,
        predicate: Callable[[Clip], bool] | None = None,
    ) -> Clip | None:
        """Like :meth:`find_clips` but returns the first match (or ``None``)."""
        if (name is None) == (predicate is None):
            raise errors.MediaError(
                "MediaPool.find_clip requires exactly one of name= or predicate=.",
            )
        check = predicate if predicate is not None else (lambda c: c.name == name)
        for folder in self.walk():
            for clip in folder.clips:
                if check(clip):
                    return clip
        return None

    def delete_folders(self, folders: Folder | Iterable[Folder]) -> None:
        """Delete one or more (sub-)folders from the pool."""
        if isinstance(folders, Folder):
            folders = [folders]
        raws = [f.raw for f in folders]
        if not raws:
            return
        if not self._raw.DeleteFolders(raws):
            raise errors.MediaError(
                f"Could not delete {len(raws)} folder(s).",
                cause="DeleteFolders returned False.",
                state={"count": len(raws)},
            )

    def delete_timelines(self, timelines: Any) -> None:
        """Delete one or more timelines from the pool.

        Accepts a :class:`~dvr.timeline.Timeline`, its name as a string, or
        an iterable of either. Wraps Resolve's ``DeleteTimelines``.
        """
        from .timeline import Timeline as _Timeline

        if isinstance(timelines, (_Timeline, str)) or not isinstance(timelines, Iterable):
            timelines = [timelines]
        raws: list[Any] = []
        for t in timelines:
            if isinstance(t, _Timeline):
                raws.append(t.raw)
            elif isinstance(t, str):
                # Look up by name on the project these belong to.
                count = (
                    self._resolve.GetTimelineCount()
                    if hasattr(self._resolve, "GetTimelineCount")
                    else 0
                )
                # `_resolve` here is the project raw handle (passed in __init__).
                raw_match = None
                for i in range(1, count + 1):
                    raw_tl = self._resolve.GetTimelineByIndex(i)
                    if raw_tl is not None and raw_tl.GetName() == t:
                        raw_match = raw_tl
                        break
                if raw_match is None:
                    raise errors.TimelineError(
                        f"No timeline named {t!r} on this project.",
                        state={"requested": t},
                    )
                raws.append(raw_match)
            else:
                raws.append(t)  # raw fusionscript handle, trust the caller
        if not raws:
            return
        if not self._raw.DeleteTimelines(raws):
            raise errors.TimelineError(
                f"Could not delete {len(raws)} timeline(s).",
                cause="DeleteTimelines returned False.",
                state={"count": len(raws)},
            )

    # Legacy alias used by the CLI.
    def _find_bin(self, name: str) -> Folder:
        return self._find_folder(name)

    def refresh(self) -> None:
        """In collaboration mode, refresh stale folders."""
        self._raw.RefreshFolders()

    # --- import / export -----------------------------------------------

    def import_media(
        self,
        paths: Iterable[str],
        *,
        folder: Folder | None = None,
    ) -> list[Clip]:
        """Import file paths into the pool. Idempotent at the path level."""
        path_list = list(paths)
        if folder is not None:
            self.set_current_folder(folder)
        result = self._raw.ImportMedia(path_list)
        if not result:
            raise errors.MediaImportError(
                "Import returned no items.",
                cause="ImportMedia returned an empty list — paths may be unreadable.",
                state={"requested_count": len(path_list), "paths": path_list},
            )
        return [Clip(it) for it in result]

    def import_imf(
        self,
        imf_dir: str,
        *,
        folder: Folder | None = None,
    ) -> list[Clip]:
        """Import an IMF (Interoperable Master Format) package into the pool.

        Pass the path to the IMF *folder* (the OV folder containing
        ``ASSETMAP.xml``, ``CPL_*.xml``, ``PKL_*.xml``, and the ``.mxf``
        essence files) — not the CPL XML itself. Resolve's
        ``MediaPool.ImportMedia([cpl_path])`` returns empty for IMFs;
        ``MediaStorage.AddItemListToMediaPool([imf_dir])`` is the working
        path and is what this method uses.

        Each MXF in the package is imported as a separate Media Pool clip
        (picture, 5.1 audio, 2.0 audio, etc.). The CPL/PKL/ASSETMAP/OPL
        XMLs are recognized and skipped automatically by Resolve.
        """
        from pathlib import Path

        imf_path = Path(imf_dir)
        if not imf_path.is_dir():
            raise errors.MediaImportError(
                f"IMF path {imf_dir!r} is not a directory.",
                fix="Pass the IMF OV folder, not the CPL XML or an MXF file.",
                state={"path": imf_dir},
            )
        if not any(imf_path.glob("CPL_*.xml")):
            raise errors.MediaImportError(
                f"No CPL_*.xml found under {imf_dir!r}; this does not look like an IMF.",
                fix="Confirm the path points at an IMP (Interoperable Master Package) folder.",
                state={"path": imf_dir},
            )
        if folder is not None:
            self.set_current_folder(folder)
        storage_raw = (
            self._resolve.GetMediaStorage() if hasattr(self._resolve, "GetMediaStorage") else None
        )
        if storage_raw is None:
            raise errors.MediaImportError(
                "Could not access MediaStorage for IMF import.",
                fix="Ensure a project is loaded before calling import_imf().",
            )
        result = storage_raw.AddItemListToMediaPool([str(imf_path)])
        if not result:
            raise errors.MediaImportError(
                f"IMF import returned no items for {imf_dir!r}.",
                cause="AddItemListToMediaPool returned an empty list.",
                fix="Check that the IMF essence files are readable and intact.",
                state={"path": imf_dir},
            )
        return [Clip(it) for it in result]

    # Legacy aliases.
    def import_(
        self,
        paths: Iterable[str],
        *,
        bin: Folder | None = None,
    ) -> list[Clip]:
        return self.import_media(paths, folder=bin)

    def import_to(
        self,
        folder: str | Folder,
        paths: Iterable[str],
        *,
        create_missing: bool = True,
    ) -> list[Clip]:
        """Idempotent "import these paths into this folder, restore previous folder when done".

        ``folder`` may be a :class:`Folder` or a folder name (string). If
        the folder doesn't exist and ``create_missing=True`` (default), it
        is created under the root.

        The current folder selection is restored after the import — useful
        when scripts shouldn't perturb the user's UI state.
        """
        previous = None
        try:
            previous = self.current_folder
        except errors.DvrError:
            previous = None

        target: Folder
        if isinstance(folder, Folder):
            target = folder
        else:
            found = self.find_folder(folder)
            if found is None:
                if not create_missing:
                    raise errors.MediaError(
                        f"No folder named {folder!r}.",
                        fix="Pass create_missing=True to auto-create.",
                        state={"requested": folder},
                    )
                target = self.add_folder(folder)
            else:
                target = found

        try:
            return self.import_media(paths, folder=target)
        finally:
            if previous is not None:
                try:
                    self.set_current_folder(previous)
                except errors.DvrError as exc:
                    logger.warning(
                        "could not restore previous folder %r: %s",
                        previous.name,
                        exc,
                    )

    def find_or_import(
        self,
        path: str | os.PathLike[str],
        *,
        folder: Folder | str | None = None,
    ) -> Clip:
        """Return the existing :class:`Clip` for ``path``, importing if absent.

        Walks the entire pool looking for a clip whose ``file_path`` matches
        the requested path (after :func:`os.path.normpath` /
        :func:`os.path.normcase`). If no match is found, imports it via
        :meth:`import_media` (or :meth:`import_to` when ``folder`` is given)
        and returns the freshly imported clip.

        This is the right primitive when a script repeatedly references the
        same source file — for example, batch-extracting many shots out of a
        single master render. Without it, every call to
        :meth:`import_media` adds a duplicate Media Pool entry for the same
        path, which slows down the project and clutters the bin tree.

        Args:
            path:   Source path on disk. May be ``str`` or ``Path``.
            folder: Optional bin to import *into* if the clip isn't already
                    in the pool. Accepts a :class:`Folder` or a folder name
                    (auto-created under the root). Has no effect when the
                    clip is found via lookup — already-pooled clips stay
                    where they are.

        Returns:
            A :class:`Clip` for the requested path.

        Raises:
            MediaImportError: if the path is not in the pool and Resolve
                refuses to import it.
        """
        target = _normalise_path(path)
        for f in self.walk():
            for c in f.clips:
                cp = c.file_path
                if cp and _normalise_path(cp) == target:
                    return c
        # Not in the pool — import. Defer to import_to when a folder is
        # specified so the previous current-folder selection is restored.
        if folder is None:
            results = self.import_media([str(path)])
        else:
            results = self.import_to(folder, [str(path)])
        if not results:
            raise errors.MediaImportError(
                f"find_or_import: import returned no clips for {path!r}.",
                cause="ImportMedia produced an empty list — path may be unreadable.",
                state={"path": str(path)},
            )
        return results[0]

    def import_timeline(
        self,
        file_path: str,
        *,
        options: dict[str, Any] | None = None,
    ) -> Timeline:
        from pathlib import Path

        from .timeline import Timeline

        opts = options or {"timelineName": Path(file_path).stem, "importSourceClips": True}
        raw = self._raw.ImportTimelineFromFile(file_path, opts)
        if raw is None:
            raise errors.InterchangeError(
                f"Could not import timeline from {file_path!r}.",
                cause="ImportTimelineFromFile returned None.",
                fix="Confirm the file is a valid AAF/EDL/XML/FCPXML/DRT/OTIO/ADL.",
                state={"file_path": file_path, "options": opts},
            )
        return Timeline(raw, self._raw.GetCurrentFolder())

    # --- timelines from clips ------------------------------------------

    def create_empty_timeline(self, name: str) -> Timeline:
        from .timeline import Timeline

        raw = self._raw.CreateEmptyTimeline(name)
        if raw is None:
            raise errors.MediaError(
                f"Could not create timeline {name!r}.",
                cause="CreateEmptyTimeline returned None — name may collide.",
                state={"name": name},
            )
        return Timeline(raw, self._raw.GetCurrentFolder())

    def create_timeline_from_clips(
        self,
        name: str,
        clips: Iterable[Clip],
    ) -> Timeline:
        from .timeline import Timeline

        raws = [c.raw for c in clips]
        raw = self._raw.CreateTimelineFromClips(name, raws)
        if raw is None:
            raise errors.MediaError(
                f"Could not create timeline {name!r} from clips.",
                cause="CreateTimelineFromClips returned None.",
                state={"name": name, "clip_count": len(raws)},
            )
        return Timeline(raw, self._raw.GetCurrentFolder())

    # Legacy alias (older name when this method took ``Asset`` objects).
    def create_timeline_from_assets(
        self,
        name: str,
        assets: Iterable[Clip],
    ) -> Timeline:
        return self.create_timeline_from_clips(name, assets)

    def append_to_timeline(
        self,
        items: Iterable[Clip | dict[str, Any]],
    ) -> list[Any]:
        """Append clips to the current timeline.

        Each item is either a :class:`Clip` or a clipInfo dict (e.g.
        ``{"mediaPoolItem": clip.raw, "startFrame": 24, "endFrame": 96}``).
        """
        payload: list[Any] = []
        for item in items:
            payload.append(item.raw if isinstance(item, Clip) else item)
        result = self._raw.AppendToTimeline(payload)
        if not result:
            raise errors.MediaError(
                "Could not append items to the current timeline.",
                cause="AppendToTimeline returned no items.",
                state={"requested_count": len(payload)},
            )
        return list(result)

    # --- selection / mutation -----------------------------------------

    def selected(self) -> list[Clip]:
        return [Clip(it) for it in (self._raw.GetSelectedClips() or [])]

    def select(self, clip: Clip) -> None:
        self._raw.SetSelectedClip(clip.raw)

    def delete_clips(self, clips: Clip | Iterable[Clip]) -> None:
        """Delete one or more clips from the media pool."""
        if isinstance(clips, Clip):
            clips = [clips]
        raws = [c.raw for c in clips]
        if not raws:
            return
        if not self._raw.DeleteClips(raws):
            raise errors.MediaError(
                f"Could not delete {len(raws)} clip(s).",
                cause="DeleteClips returned False.",
                state={"count": len(raws)},
            )

    # Legacy alias.
    def delete(self, clips: Clip | Iterable[Clip]) -> None:
        self.delete_clips(clips)

    def move(self, clips: Iterable[Clip], target: Folder) -> None:
        """Move clips into a target folder."""
        raws = [c.raw for c in clips]
        if not raws:
            return
        if not self._raw.MoveClips(raws, target.raw):
            raise errors.MediaError(
                f"Could not move {len(raws)} clip(s) to {target.name!r}.",
                state={"count": len(raws), "target_folder": target.name},
            )

    def relink(self, clips: Iterable[Clip], folder: str) -> None:
        """Relink clips to a folder of replacement files on disk."""
        raws = [c.raw for c in clips]
        if not raws:
            return
        if not self._raw.RelinkClips(raws, folder):
            raise errors.MediaError(
                f"Could not relink {len(raws)} clip(s) to {folder!r}.",
                cause="RelinkClips returned False — folder may not contain matching files.",
                state={"count": len(raws), "folder": folder},
            )

    def unlink(self, clips: Iterable[Clip]) -> None:
        raws = [c.raw for c in clips]
        if not raws:
            return
        if not self._raw.UnlinkClips(raws):
            raise errors.MediaError(
                "Could not unlink clip(s).",
                state={"count": len(raws)},
            )

    # --- audio sync ---------------------------------------------------

    def auto_sync_audio(
        self,
        clips: Iterable[Clip],
        *,
        sync_settings: dict[str, Any] | None = None,
    ) -> None:
        raws = [c.raw for c in clips]
        ok = (
            self._raw.AutoSyncAudio(raws, sync_settings)
            if sync_settings
            else self._raw.AutoSyncAudio(raws)
        )
        if not ok:
            raise errors.MediaError(
                f"Could not auto-sync audio for {len(raws)} clip(s).",
                cause="AutoSyncAudio returned False.",
                state={"count": len(raws), "sync_settings": sync_settings},
            )

    # --- subclip workflows -------------------------------------------

    def create_subclip(
        self,
        source_path: str,
        *,
        start: int,
        end: int,
        name: str | None = None,
        folder: Folder | str | None = None,
    ) -> Clip:
        """Import ``source_path`` as a sub-clip with explicit frame range.

        Unlike :meth:`import_with_subclips` (which takes raw dicts), this
        is a typed primitive: ``(source_path, start_frame, end_frame, name)
        → Clip``. Useful for EDL-driven ingestion where one master file
        spawns many sub-clips.

        Resolve renames the imported clip to ``name`` if given. The clip
        is placed in ``folder`` (a :class:`Folder`, name string, or
        ``None`` for the current folder).
        """
        item: dict[str, Any] = {
            "FilePath": source_path,
            "StartIndex": int(start),
            "EndIndex": int(end),
        }
        if name is not None:
            # Resolve honours a "ClipName" hint on AddItemListToMediaPool
            # for some media types; we additionally rename via SetClipProperty
            # below for guaranteed effect.
            item["ClipName"] = name

        previous: Folder | None = None
        if folder is not None:
            try:
                previous = self.current_folder
            except errors.DvrError:
                previous = None
            target = folder if isinstance(folder, Folder) else self._find_folder(folder)
            self.set_current_folder(target)

        try:
            clips = self.import_with_subclips([item])
        finally:
            if folder is not None and previous is not None:
                try:
                    self.set_current_folder(previous)
                except errors.DvrError as exc:
                    logger.warning(
                        "could not restore previous folder %r: %s",
                        previous.name,
                        exc,
                    )

        if not clips:
            raise errors.MediaImportError(
                f"Could not create sub-clip from {source_path!r}.",
                cause="AddItemListToMediaPool returned no items.",
                state={"source_path": source_path, "start": start, "end": end},
            )
        clip = clips[0]
        if name is not None and clip.name != name:
            import contextlib

            # Non-fatal — Resolve sometimes rejects rename right after
            # import; the file/index data is still correct.
            with contextlib.suppress(errors.MediaError):
                clip.name = name
        return clip

    def import_with_subclips(
        self,
        items: Iterable[dict[str, Any]],
        *,
        folder: Folder | None = None,
    ) -> list[Clip]:
        """Import paths with explicit per-clip frame ranges.

        Each entry is a dict ``{"FilePath": str, "StartIndex": int,
        "EndIndex": int}``; pass-through to Resolve's
        ``MediaStorage.AddItemListToMediaPool`` which honours the indices
        to create sub-clips. Useful for EDL-driven ingestion where a single
        master file feeds many clips.
        """
        item_list = list(items)
        if folder is not None:
            self.set_current_folder(folder)
        # Resolve handles this through MediaStorage, not MediaPool. We
        # walk through the project's MediaStorage handle to keep the
        # call site uniform on this object.
        storage_raw = (
            self._resolve.GetMediaStorage() if hasattr(self._resolve, "GetMediaStorage") else None
        )
        if storage_raw is None:
            raise errors.MediaImportError(
                "Could not access MediaStorage for sub-clip import.",
                fix="Ensure a project is loaded; sub-clip import requires a project context.",
            )
        result = storage_raw.AddItemListToMediaPool(item_list)
        if not result:
            raise errors.MediaImportError(
                "Sub-clip import returned no items.",
                cause="AddItemListToMediaPool returned an empty list.",
                state={"requested_count": len(item_list)},
            )
        return [Clip(it) for it in result]

    # --- inspection ----------------------------------------------------

    def inspect(self) -> dict[str, Any]:
        root = self.root
        try:
            current_name = self.current_folder.name
        except errors.DvrError:
            current_name = None
        return {
            "current_folder": current_name,
            "current_bin": current_name,  # legacy key — same value as current_folder
            "root": root.inspect(),
            "selected_count": len(self.selected()),
        }


def _normalise_path(p: str | os.PathLike[str]) -> str:
    """Normalize ``p`` for cross-platform equality comparison.

    Applies :func:`os.path.normpath` (collapses ``.``/``..`` segments) and
    :func:`os.path.normcase` (lower-cases on Windows, no-op on POSIX). Used
    by :meth:`MediaPool.find_or_import` to dedup against the pool's
    Resolve-reported ``File Path`` strings.
    """
    return os.path.normcase(os.path.normpath(str(Path(p))))


__all__ = [
    "Asset",
    "Bin",
    "Clip",
    "Folder",
    "MediaPool",
    "MediaPoolItem",
    "MediaStorage",
]
