"""Gallery (still albums and PowerGrade albums) wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List  # noqa: UP035

from . import errors

if TYPE_CHECKING:
    from .project import Project


class Still:
    """A single gallery still."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def label(self) -> str:
        return str(self._raw.GetLabel() or "")

    @label.setter
    def label(self, value: str) -> None:
        if not self._raw.SetLabel(value):
            raise errors.DvrError(
                f"Could not set still label to {value!r}.",
            )


class Album:
    """A gallery album (still or PowerGrade)."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def name(self) -> str:
        return str(self._raw.GetAlbumName())

    @name.setter
    def name(self, value: str) -> None:
        if not self._raw.SetAlbumName(value):
            raise errors.DvrError(
                f"Could not rename album to {value!r}.",
            )

    def stills(self) -> list[Still]:
        return [Still(s) for s in (self._raw.GetStills() or [])]

    def export_stills(
        self,
        stills: list[Still],
        folder: str,
        prefix: str = "",
        *,
        format: str = "png",
    ) -> None:
        """Export stills to ``folder``. Format: dpx | cin | tif | jpg | png | ppm | bmp | xpm."""
        if not self._raw.ExportStills([s.raw for s in stills], folder, prefix, format):
            raise errors.DvrError(
                "Could not export stills.",
                state={"folder": folder, "format": format, "count": len(stills)},
            )

    def import_stills(self, file_paths: list[str]) -> None:
        if not self._raw.ImportStills(file_paths):
            raise errors.DvrError(
                "Could not import stills.",
                state={"count": len(file_paths)},
            )

    def delete_stills(self, stills: list[Still]) -> None:
        if not self._raw.DeleteStills([s.raw for s in stills]):
            raise errors.DvrError(
                "Could not delete stills.",
                state={"count": len(stills)},
            )

    def inspect(self) -> dict[str, Any]:
        return {"name": self.name, "still_count": len(self.stills())}


class Gallery:
    """The project-scoped gallery (stills + PowerGrades)."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def raw(self) -> Any:
        return self._raw

    def still_albums(self) -> List[Album]:  # noqa: UP006
        return [Album(a) for a in (self._raw.GetGalleryStillAlbums() or [])]

    def powergrade_albums(self) -> List[Album]:  # noqa: UP006
        return [Album(a) for a in (self._raw.GetGalleryPowerGradeAlbums() or [])]

    def current_album(self) -> Album | None:
        raw = self._raw.GetCurrentStillAlbum()
        return Album(raw) if raw else None

    def set_current_album(self, album: Album) -> None:
        if not self._raw.SetCurrentStillAlbum(album.raw):
            raise errors.DvrError(
                f"Could not set current still album to {album.name!r}.",
            )

    def create_still_album(self, name: str) -> Album:
        raw = self._raw.CreateGalleryStillAlbum(name)
        if raw is None:
            raise errors.DvrError(f"Could not create still album {name!r}.")
        return Album(raw)

    def create_powergrade_album(self, name: str) -> Album:
        raw = self._raw.CreateGalleryPowerGradeAlbum(name)
        if raw is None:
            raise errors.DvrError(f"Could not create PowerGrade album {name!r}.")
        return Album(raw)

    def inspect(self) -> dict[str, Any]:
        current = self.current_album()
        return {
            "still_albums": [a.name for a in self.still_albums()],
            "powergrade_albums": [a.name for a in self.powergrade_albums()],
            "current_album": current.name if current else None,
        }


def gallery_for(project: Project) -> Gallery:
    """Get the :class:`Gallery` for a project."""
    raw = project.raw.GetGallery()
    if raw is None:
        raise errors.DvrError(
            f"No gallery for project {project.name!r}.",
            cause="GetGallery returned None.",
        )
    return Gallery(raw)


__all__ = ["Album", "Gallery", "Still", "gallery_for"]
