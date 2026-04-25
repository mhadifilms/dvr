"""Project and project-manager wrappers.

A :class:`Project` is the unit Resolve loads, saves, archives, and
imports/exports as a ``.drp``. The :class:`ProjectNamespace` exposed at
``Resolve.project`` is the entry point: it lets you list, load, create,
ensure, delete, and switch between projects.

Idempotent operations:

* :meth:`ProjectNamespace.ensure` — get-or-create. Always returns a
  :class:`Project`. Never raises "already exists".
* :meth:`ProjectNamespace.use` — context manager that switches to a
  project for the duration of a ``with`` block, restoring the previous
  current project on exit.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, List  # noqa: UP035 — `List` avoids `list` method shadow

from . import errors

if TYPE_CHECKING:
    from .gallery import Gallery
    from .media import MediaPool
    from .timeline import TimelineNamespace

logger = logging.getLogger("dvr.project")


class Project:
    """A single Resolve project (loaded or otherwise reachable)."""

    def __init__(self, raw: Any, manager: Any) -> None:
        self._raw = raw
        self._manager = manager

    @property
    def name(self) -> str:
        return self._raw.GetName()

    @property
    def raw(self) -> Any:
        return self._raw

    # --- settings ---------------------------------------------------------

    def get_setting(self, key: str | None = None) -> Any:
        """Return a single setting (or all of them if ``key`` is None)."""
        return self._raw.GetSetting(key) if key else self._raw.GetSetting()

    def set_setting(self, key: str, value: Any) -> None:
        """Set a project setting; raise :class:`SettingsError` on failure."""
        ok = self._raw.SetSetting(key, str(value))
        if not ok:
            current = self._raw.GetSetting(key)
            raise errors.SettingsError(
                f"Could not set project setting {key!r} to {value!r}.",
                cause=(
                    f"SetSetting returned False. Current value is {current!r}; "
                    "the value may be of the wrong type, or this key may not exist "
                    "on this Resolve version."
                ),
                fix=(
                    "Run `dvr schema settings` to see valid keys and values, "
                    "or check Resolve's API docs for the expected type."
                ),
                state={"key": key, "value": value, "current": current},
            )

    # --- save / close -----------------------------------------------------

    def save(self) -> None:
        if not self._manager.SaveProject():
            raise errors.ProjectError(
                f"Failed to save project {self.name!r}.",
                cause="SaveProject() returned False.",
                fix="Check disk space and that the project is not read-only.",
                state={"project": self.name},
            )

    def close(self) -> None:
        if not self._manager.CloseProject(self._raw):
            raise errors.ProjectError(
                f"Failed to close project {self.name!r}.",
                cause="CloseProject() returned False.",
                state={"project": self.name},
            )

    # --- typed settings accessor -----------------------------------------

    @property
    def settings(self) -> Settings:
        """Typed proxy for project settings.

        Read with attribute access (``proj.settings.timeline_resolution_width``)
        and write the same way. Falls back to string-key get/set for any
        unknown attribute name. Common settings are exposed as typed
        properties; everything else uses ``proj.get_setting(key)`` /
        ``proj.set_setting(key, value)`` directly.
        """
        return Settings(self)

    # --- domain accessors -------------------------------------------------

    @property
    def timeline(self) -> TimelineNamespace:
        """Timeline namespace — current, list, ensure, switch, ``use()`` context manager.

        Also iterable: ``for tl in project.timeline: ...``. Index/name
        lookup: ``project.timeline["ROUND_2"]``, ``project.timeline[0]``.
        """
        from .timeline import TimelineNamespace

        return TimelineNamespace(self)

    @property
    def timelines(self) -> TimelineNamespace:
        """Plural alias for :attr:`timeline` — reads more naturally in loops."""
        from .timeline import TimelineNamespace

        return TimelineNamespace(self)

    @property
    def current_timeline(self) -> Any:
        """The currently active timeline, or ``None``. Settable.

        Equivalent to ``project.timeline.current`` (read) and
        ``project.timeline.set_current(...)`` (write). Accepts a
        :class:`Timeline` or its name as a string.
        """
        return self.timeline.current

    @current_timeline.setter
    def current_timeline(self, value: Any) -> None:
        self.timeline.set_current(value)

    @property
    def media(self) -> MediaPool:
        """Wrapped media pool for this project."""
        from .media import MediaPool

        raw = self._raw.GetMediaPool()
        if raw is None:
            raise errors.ProjectError(
                "Could not get the project's media pool.",
                cause="GetMediaPool() returned None.",
                state={"project": self.name},
            )
        return MediaPool(raw, self._raw)

    @property
    def media_pool(self) -> Any:
        """Raw ``MediaPool`` handle. Prefer :attr:`media` in new code."""
        return self._raw.GetMediaPool()

    @property
    def gallery(self) -> Gallery:
        """The project's gallery (still and PowerGrade albums)."""
        from .gallery import gallery_for

        return gallery_for(self)

    # --- inspection -------------------------------------------------------

    def inspect(self) -> dict[str, Any]:
        timeline_count = self._raw.GetTimelineCount()
        current_timeline = self._raw.GetCurrentTimeline()
        timelines = []
        for i in range(1, timeline_count + 1):
            tl = self._raw.GetTimelineByIndex(i)
            if tl is not None:
                timelines.append(tl.GetName())
        return {
            "name": self.name,
            "timeline_count": timeline_count,
            "current_timeline": current_timeline.GetName() if current_timeline else None,
            "timelines": timelines,
        }


class ProjectNamespace:
    """Project-manager operations exposed via :attr:`Resolve.project`."""

    def __init__(self, resolve_raw: Any, manager: Any) -> None:
        self._resolve = resolve_raw
        self._manager = manager

    # --- read -------------------------------------------------------------

    @property
    def current(self) -> Project | None:
        raw = self._manager.GetCurrentProject()
        return Project(raw, self._manager) if raw is not None else None

    def list(self) -> List[str]:  # noqa: UP006
        """Return project names in the current PM folder."""
        return [str(n) for n in (self._manager.GetProjectListInCurrentFolder() or [])]

    def folders(self) -> List[str]:  # noqa: UP006
        """Return PM subfolder names in the current folder."""
        return [str(n) for n in (self._manager.GetFolderListInCurrentFolder() or [])]

    # --- mutate -----------------------------------------------------------

    def create(self, name: str) -> Project:
        raw = self._manager.CreateProject(name)
        if raw is None:
            raise errors.ProjectError(
                f"Could not create project {name!r}.",
                cause=(
                    "CreateProject returned None — usually because a project "
                    "with this name already exists in this PM folder."
                ),
                fix=f"Use `resolve.project.ensure({name!r})` for get-or-create semantics.",
                state={"name": name, "folder_listing": self.list()},
            )
        return Project(raw, self._manager)

    def load(self, name: str) -> Project:
        # Don't reload if it's already current.
        current = self._manager.GetCurrentProject()
        if current is not None and current.GetName() == name:
            return Project(current, self._manager)
        raw = self._manager.LoadProject(name)
        if raw is None:
            raise errors.ProjectError(
                f"Could not load project {name!r}.",
                cause="LoadProject returned None — the project may not exist in this PM folder.",
                fix=(
                    "Check available projects with `resolve.project.list()`, or navigate "
                    "into the right PM folder first."
                ),
                state={"name": name, "folder_listing": self.list()},
            )
        return Project(raw, self._manager)

    def ensure(self, name: str) -> Project:
        """Load the project if it exists, otherwise create it."""
        if name in self.list():
            return self.load(name)
        return self.create(name)

    def delete(self, name: str) -> None:
        if not self._manager.DeleteProject(name):
            raise errors.ProjectError(
                f"Could not delete project {name!r}.",
                cause="DeleteProject returned False — the project may be currently loaded.",
                fix="Close the project first (`resolve.project.current.close()`).",
                state={"name": name},
            )

    def archive(
        self,
        name: str,
        path: str,
        *,
        media: bool = True,
        cache: bool = False,
        proxy: bool = False,
    ) -> None:
        ok = self._manager.ArchiveProject(name, path, media, cache, proxy)
        if not ok:
            raise errors.ProjectError(
                f"Failed to archive project {name!r}.",
                cause="ArchiveProject returned False.",
                fix="Check that the destination path is writable.",
                state={"name": name, "path": path, "include_media": media},
            )

    def import_(self, file_path: str, name: str | None = None) -> Project:
        ok = (
            self._manager.ImportProject(file_path, name)
            if name
            else self._manager.ImportProject(file_path)
        )
        if not ok:
            raise errors.ProjectError(
                f"Failed to import project from {file_path!r}.",
                cause="ImportProject returned False.",
                fix="Check that the file is a valid .drp and that the name is unique.",
                state={"file_path": file_path, "requested_name": name},
            )
        target = name or _guess_drp_name(file_path)
        return self.load(target)

    def export(self, name: str, file_path: str, *, with_stills_and_luts: bool = True) -> None:
        ok = self._manager.ExportProject(name, file_path, with_stills_and_luts)
        if not ok:
            raise errors.ProjectError(
                f"Failed to export project {name!r} to {file_path!r}.",
                cause="ExportProject returned False.",
                fix="Check write permissions and that the project name exists.",
                state={"name": name, "file_path": file_path},
            )

    # --- context manager --------------------------------------------------

    @contextmanager
    def use(self, name: str) -> Iterator[Project]:
        """Switch to ``name`` for the duration of the ``with`` block."""
        previous = self.current
        previous_name = previous.name if previous else None
        project = self.ensure(name)
        try:
            yield project
        finally:
            if previous_name and previous_name != name:
                # Best-effort restore; don't mask exceptions from the body.
                try:
                    self.load(previous_name)
                except errors.DvrError as exc:
                    logger.warning("could not restore previous project %r: %s", previous_name, exc)


def _guess_drp_name(path: str) -> str:
    """Default project name when ``ImportProject`` is given no explicit name."""
    from pathlib import Path

    return Path(path).stem


# ---------------------------------------------------------------------------
# Typed settings proxy
# ---------------------------------------------------------------------------


# Map snake_case attribute → Resolve string key. Keep this small and
# focused on settings an integration consumer / common workflows actually touch.
# Anything missing is reachable via proj.get_setting/set_setting.
_SETTING_KEYS: dict[str, str] = {
    "timeline_resolution_width": "timelineResolutionWidth",
    "timeline_resolution_height": "timelineResolutionHeight",
    "timeline_frame_rate": "timelineFrameRate",
    "timeline_drop_frame": "timelineDropFrameTimecode",
    "timeline_use_custom_settings": "timelineUseCustomSettings",
    "timeline_playback_frame_rate": "timelinePlaybackFrameRate",
    "color_science_mode": "colorScienceMode",
    "color_space_input": "colorSpaceInput",
    "color_space_timeline": "colorSpaceTimeline",
    "color_space_output": "colorSpaceOutput",
    "tone_mapping_method": "toneMappingMethod",
    "tone_mapping_max_input_nits": "toneMappingMaxInputNits",
    "tone_mapping_max_output_nits": "toneMappingMaxOutputNits",
    "video_monitor_format": "videoMonitorFormat",
    "video_data_levels": "videoDataLevels",
    "use_color_space_aware_grading_tools": "useColorSpaceAwareGradingTools",
    "use_inverse_dpx_tone_mapping": "useInverseDPXToneMapping",
    "rcm_preset_mode": "rcmPresetMode",
    "separate_color_space_and_gamma": "separateColorSpaceAndGamma",
}


class Settings:
    """Typed proxy for project settings.

    Attribute access maps snake_case names to Resolve setting keys when a
    mapping exists; otherwise it passes through the raw string key. Use
    ``in`` to check for a known mapped attribute::

        proj.settings.timeline_resolution_width = 3840
        if "color_science_mode" in proj.settings:
            ...

    Falls through to :meth:`Project.get_setting` / :meth:`Project.set_setting`
    for everything, so unknown keys still work as long as Resolve accepts
    them.
    """

    def __init__(self, project: Project) -> None:
        # Use object.__setattr__ to bypass our own __setattr__.
        object.__setattr__(self, "_project", project)

    def __contains__(self, name: str) -> bool:
        return name in _SETTING_KEYS

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        key = _SETTING_KEYS.get(name, name)
        value = self._project.get_setting(key)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        key = _SETTING_KEYS.get(name, name)
        self._project.set_setting(key, value)

    def get(self, key: str, default: Any = None) -> Any:
        """Look up by raw Resolve key (string), with a default."""
        try:
            value = self._project.get_setting(key)
        except errors.DvrError:
            return default
        return value if value is not None and value != "" else default

    def keys(self) -> list[str]:
        """Mapped snake_case attribute names. Not exhaustive."""
        return list(_SETTING_KEYS.keys())

    def as_dict(self) -> dict[str, Any]:
        """Return a flat snapshot of all project settings (string-keyed)."""
        result = self._project.get_setting()
        return dict(result) if isinstance(result, dict) else {}


__all__ = ["Project", "ProjectNamespace", "Settings"]
