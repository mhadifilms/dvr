"""Render queue, jobs, and progress monitoring.

The Resolve render API is poll-only — there is no event stream. We turn
that into something usable in three ways:

* :class:`RenderJob` wraps a single job ID and exposes ``status``,
  ``percent``, ``eta``, and ``wait()`` with stall detection.
* :class:`RenderNamespace.watch` is a generator that yields structured
  status events (``{"type": "progress", ...}``, ``{"type": "complete",
  ...}``) as the render proceeds.
* :class:`RenderNamespace.submit` configures a job, queues it, optionally
  starts it, and returns a :class:`RenderJob` you can ``.wait()`` on.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from . import errors

if TYPE_CHECKING:
    from .timeline import TimelineItem

logger = logging.getLogger("dvr.render")


# Sentinel for "no progress in N seconds → stalled".
_DEFAULT_STALL_SECONDS = 300.0


class RenderJob:
    """A single job in Resolve's render queue."""

    def __init__(self, namespace: RenderNamespace, job_id: str) -> None:
        self._ns = namespace
        self._job_id = job_id

    @property
    def id(self) -> str:
        return self._job_id

    @property
    def status(self) -> str:
        return self._ns._project_raw.GetRenderJobStatus(self._job_id).get("JobStatus", "Unknown")

    @property
    def percent(self) -> float:
        """Completion percentage as a float in ``[0, 100]``."""
        s = self._ns._project_raw.GetRenderJobStatus(self._job_id) or {}
        return float(s.get("CompletionPercentage", 0))

    @property
    def progress(self) -> float:
        """Completion fraction in ``[0.0, 1.0]`` — same data as :attr:`percent`/100."""
        return self.percent / 100.0

    @property
    def is_finished(self) -> bool:
        """True iff the job has reached a terminal state (Complete/Failed/Cancelled)."""
        return self.status in ("Complete", "Failed", "Cancelled")

    @property
    def is_complete(self) -> bool:
        return self.status == "Complete"

    @property
    def is_failed(self) -> bool:
        return self.status == "Failed"

    def poll(self) -> dict[str, Any]:
        """Non-blocking status snapshot — same payload as :meth:`inspect`.

        Use this from a loop or scheduler when you don't want to block on
        :meth:`wait`. Returns the structured dict:
        ``{id, status, percent, progress, eta_seconds, output_path, error,
        is_finished}``.
        """
        s = self.status_dict()
        percent = float(s.get("CompletionPercentage", 0))
        return {
            "id": self._job_id,
            "status": s.get("JobStatus", "Unknown"),
            "percent": percent,
            "progress": percent / 100.0,
            "eta_seconds": (
                float(s["EstimatedTimeRemainingInMs"]) / 1000.0
                if "EstimatedTimeRemainingInMs" in s
                else None
            ),
            "output_path": self.output_path,
            "error": s.get("Error"),
            "is_finished": s.get("JobStatus") in ("Complete", "Failed", "Cancelled"),
        }

    @property
    def eta_seconds(self) -> float | None:
        s = self._ns._project_raw.GetRenderJobStatus(self._job_id) or {}
        ms = s.get("EstimatedTimeRemainingInMs")
        return float(ms) / 1000.0 if ms is not None else None

    @property
    def output_path(self) -> str | None:
        for job in self._ns._project_raw.GetRenderJobList() or []:
            if job.get("JobId") == self._job_id:
                return job.get("OutputFilename")
        return None

    def cancel(self) -> None:
        self._ns._project_raw.DeleteRenderJob(self._job_id)

    def status_dict(self) -> dict[str, Any]:
        return dict(self._ns._project_raw.GetRenderJobStatus(self._job_id) or {})

    def inspect(self) -> dict[str, Any]:
        return self.poll()

    def wait(
        self,
        *,
        poll_interval: float = 1.0,
        timeout: float | None = None,
        stall_seconds: float = _DEFAULT_STALL_SECONDS,
    ) -> RenderJob:
        """Block until this job finishes; return self.

        Raises :class:`RenderError` on failure or timeout/stall.
        """
        start = time.monotonic()
        last_pct = -1.0
        last_progress_at = start

        while True:
            s = self.status_dict()
            status = s.get("JobStatus", "Unknown")
            pct = float(s.get("CompletionPercentage", 0))

            if status == "Complete":
                return self
            if status == "Failed":
                raise errors.RenderJobError(
                    f"Render job {self._job_id} failed.",
                    cause=s.get("Error", "Resolve did not provide an error message."),
                    state={"job_id": self._job_id, "status": s},
                )
            if status == "Cancelled":
                raise errors.RenderJobError(
                    f"Render job {self._job_id} was cancelled.",
                    state={"job_id": self._job_id},
                )

            if pct > last_pct:
                last_pct = pct
                last_progress_at = time.monotonic()

            now = time.monotonic()
            if timeout is not None and now - start > timeout:
                raise errors.RenderError(
                    f"Render job {self._job_id} exceeded timeout of {timeout:.0f}s.",
                    cause="The render did not complete in the allotted time.",
                    state={"job_id": self._job_id, "elapsed_s": now - start, "percent": pct},
                )
            if now - last_progress_at > stall_seconds:
                raise errors.RenderError(
                    f"Render job {self._job_id} appears stalled.",
                    cause=f"No progress for {stall_seconds:.0f} seconds.",
                    fix="Inspect Resolve for a hung dialog or unresponsive UI.",
                    state={"job_id": self._job_id, "percent": pct},
                )

            time.sleep(poll_interval)


class RenderNamespace:
    """Render queue operations exposed at :attr:`Resolve.render`."""

    def __init__(self, resolve: Any) -> None:
        self._resolve = resolve  # dvr.Resolve
        current = self._resolve.project.current
        if current is None:
            raise errors.ProjectError(
                "No project is currently loaded.",
                fix="Load or create a project before submitting renders.",
            )
        self._project_raw = current.raw

    # --- formats / codecs / presets --------------------------------------

    def formats(self) -> dict[str, str]:
        return dict(self._project_raw.GetRenderFormats() or {})

    def codecs(self, format_name: str) -> dict[str, str]:
        return dict(self._project_raw.GetRenderCodecs(format_name) or {})

    def current_format_and_codec(self) -> dict[str, str]:
        return dict(self._project_raw.GetCurrentRenderFormatAndCodec() or {})

    def set_format_and_codec(self, format_name: str, codec: str) -> None:
        """Set render container format and codec, with read-back verification.

        Resolve's ``SetCurrentRenderFormatAndCodec`` returns None on both
        success and failure, so we verify by reading back the current
        pair. If the requested values didn't take, raise a structured
        error with the actual current state.
        """
        self._project_raw.SetCurrentRenderFormatAndCodec(format_name, codec)
        actual = self.current_format_and_codec()
        if actual.get("format") != format_name or actual.get("codec") != codec:
            raise errors.RenderError(
                f"Could not set render format/codec to {format_name!r}/{codec!r}.",
                cause=(
                    "SetCurrentRenderFormatAndCodec was silently rejected — "
                    "the format/codec pair may be unsupported on this Resolve build."
                ),
                fix=(
                    f"Inspect available pairs with `dvr render formats` / "
                    f"`dvr render codecs {format_name}`."
                ),
                state={"requested": {"format": format_name, "codec": codec}, "actual": actual},
            )

    def presets(self) -> list[str]:
        return list(self._project_raw.GetRenderPresetList() or [])

    def load_preset(self, name: str) -> None:
        if not self._project_raw.LoadRenderPreset(name):
            raise errors.RenderError(
                f"Could not load render preset {name!r}.",
                cause="LoadRenderPreset returned False — preset may not exist.",
                fix=f"Available presets: {self.presets()}",
                state={"requested": name},
            )

    def save_preset(self, name: str) -> None:
        """Save the current render settings as a new render preset."""
        if not self._project_raw.SaveAsNewRenderPreset(name):
            raise errors.RenderError(
                f"Could not save render preset {name!r}.",
                cause=(
                    "SaveAsNewRenderPreset returned False — a preset with this "
                    "name may already exist or render settings are invalid."
                ),
                fix="Try a different name or call set_format_and_codec() first.",
                state={"requested": name, "existing": self.presets()},
            )

    def delete_preset(self, name: str) -> None:
        """Delete a saved render preset."""
        if not self._project_raw.DeleteRenderPreset(name):
            raise errors.RenderError(
                f"Could not delete render preset {name!r}.",
                cause="DeleteRenderPreset returned False — preset may not exist.",
                state={"requested": name, "existing": self.presets()},
            )

    def export_preset(self, name: str, file_path: str) -> None:
        """Export a render preset to a ``.xml`` file for backup or sharing."""
        if not self._project_raw.ExportRenderPreset(name, file_path):
            raise errors.RenderError(
                f"Could not export render preset {name!r} to {file_path!r}.",
                state={"requested": name, "file_path": file_path},
            )

    def import_preset(self, file_path: str) -> None:
        """Import a render preset from a ``.xml`` file."""
        if not self._project_raw.ImportRenderPreset(file_path):
            raise errors.RenderError(
                f"Could not import render preset from {file_path!r}.",
                cause="ImportRenderPreset returned False — file may be missing or invalid.",
                state={"file_path": file_path},
            )

    # --- queue ------------------------------------------------------------

    def queue(self) -> list[dict[str, Any]]:
        return list(self._project_raw.GetRenderJobList() or [])

    def is_rendering(self) -> bool:
        return bool(self._project_raw.IsRenderingInProgress())

    def stop(self) -> None:
        self._project_raw.StopRendering()

    def clear(self) -> None:
        self._project_raw.DeleteAllRenderJobs()

    # --- submit -----------------------------------------------------------

    def submit(
        self,
        *,
        target_dir: str,
        custom_name: str | None = None,
        preset: str | None = None,
        format: str | None = None,
        codec: str | None = None,
        settings: dict[str, Any] | None = None,
        start: bool = True,
    ) -> RenderJob:
        """Configure and queue a render of the current timeline.

        Args:
            target_dir:  Output directory (must exist).
            custom_name: Filename without extension. Defaults to timeline name.
            preset:      Render preset to load before applying overrides.
            format:      Container format (``mov``, ``mxf`` ...).
            codec:       Codec name (e.g. ``ProRes4444XQ``).
            settings:    Extra ``SetRenderSettings`` keys (e.g. ``{"MarkIn": 24}``).
            start:       If True, start rendering immediately.

        Returns:
            A :class:`RenderJob`. If ``start=False``, the job is queued
            but not yet running; call :meth:`RenderJob.wait` after starting.
        """
        # Resolve refuses queue mutations while a render is in progress —
        # all the relevant calls return None, leaving the caller staring
        # at "SetRenderSettings returned False" with no useful context.
        # Detect it up front and raise something actionable.
        if self.is_rendering():
            raise errors.RenderError(
                "Cannot configure a render — Resolve is currently rendering.",
                cause="IsRenderingInProgress() returned True.",
                fix=(
                    "Wait for the in-progress job to finish (use `dvr render watch`), "
                    "or cancel it with `r.render.stop()`."
                ),
                state={"queue_size": len(self.queue())},
            )

        # Switch to deliver page so render queue operations are reliable.
        # Tolerated as a no-op on headless instances — see _open_page.
        self._resolve.app.page = "deliver"

        if preset:
            self.load_preset(preset)
        if format and codec:
            self.set_format_and_codec(format, codec)

        merged: dict[str, Any] = {"SelectAllFrames": True, "TargetDir": target_dir}
        if custom_name:
            merged["CustomName"] = custom_name
        if settings:
            merged.update(settings)

        # SetRenderSettings returns None on some Resolve builds even on
        # success — fall back to checking whether a job actually got
        # queued by AddRenderJob a few lines below.
        ok_settings = self._project_raw.SetRenderSettings(merged)
        if ok_settings is False:
            raise errors.RenderError(
                "Could not apply render settings.",
                cause="SetRenderSettings returned False.",
                fix="Check that target_dir exists and the requested keys are valid.",
                state={"settings": merged},
            )

        before = {j["JobId"] for j in self.queue()}
        self._project_raw.AddRenderJob()
        after = self.queue()
        new_jobs = [j["JobId"] for j in after if j["JobId"] not in before]
        if not new_jobs:
            raise errors.RenderError(
                "Could not add a render job.",
                cause=(
                    "AddRenderJob produced no new entry. Common causes: no current timeline; "
                    "format/codec invalid; an unsaved render is open."
                ),
                fix="Verify a timeline is loaded and the format/codec pair is supported.",
                state={
                    "queue_size": len(after),
                    "format_codec": self.current_format_and_codec(),
                },
            )
        job = RenderJob(self, new_jobs[0])

        if start:
            ok_start = self._project_raw.StartRendering([job.id], False)
            if ok_start is False:
                raise errors.RenderError(
                    f"Could not start render job {job.id}.",
                    cause="StartRendering returned False.",
                    state={"job_id": job.id},
                )
            # Returning None — verify the render actually started. Some
            # Resolve builds report None even on success; the queue
            # status will flip to Rendering shortly.

        return job

    # --- batch / per-clip submit -----------------------------------------

    def submit_per_clip(
        self,
        items: Iterable[TimelineItem],
        *,
        target_dir: str,
        naming_template: str = "{clip_name}",
        preset: str | None = None,
        format: str | None = None,
        codec: str | None = None,
        settings: dict[str, Any] | None = None,
        start: bool = True,
    ) -> list[RenderJob]:
        """Queue one render job per timeline item, with the timeline marks
        constrained to that item's frame range.

        ``naming_template`` is a Python format string; supported keys:

        * ``{clip_name}`` — the timeline item's name
        * ``{index}`` — 1-based position in ``items``
        * ``{start}`` / ``{end}`` — record-frame bounds
        * ``{track}`` — track index (1-based)

        Each job uses the same target directory; the per-job filename is
        derived from the template. Returns the list of submitted
        :class:`RenderJob` objects in the same order as ``items``.

        With ``start=True`` (default), Resolve picks them up sequentially
        in the order added. Use :meth:`watch` on the returned IDs.
        """
        item_list = list(items)
        if not item_list:
            return []

        if self.is_rendering():
            raise errors.RenderError(
                "Cannot configure per-clip renders — Resolve is currently rendering.",
                cause="IsRenderingInProgress() returned True.",
                fix=(
                    "Wait for the in-progress job to finish (use `dvr render watch`), "
                    "or cancel it with `r.render.stop()`."
                ),
                state={"queue_size": len(self.queue())},
            )

        # Switch once. Tolerated as a no-op on headless Resolve.
        self._resolve.app.page = "deliver"
        if preset:
            self.load_preset(preset)
        if format and codec:
            self.set_format_and_codec(format, codec)

        jobs: list[RenderJob] = []
        for index, item in enumerate(item_list, 1):
            custom_name = naming_template.format(
                clip_name=item.name,
                index=index,
                start=item.start,
                end=item.end,
                track=item.track_index,
            )
            merged: dict[str, Any] = {
                "TargetDir": target_dir,
                "CustomName": custom_name,
                "MarkIn": int(item.start),
                "MarkOut": int(item.end),
                # Render only the in/out range, not the whole timeline.
                "SelectAllFrames": False,
            }
            if settings:
                merged.update(settings)
            ok_settings = self._project_raw.SetRenderSettings(merged)
            if ok_settings is False:
                raise errors.RenderError(
                    "Could not apply per-clip render settings.",
                    cause="SetRenderSettings returned False.",
                    state={"settings": merged, "clip_name": item.name},
                )
            before = {j["JobId"] for j in self.queue()}
            self._project_raw.AddRenderJob()
            after = self.queue()
            new_jobs = [j["JobId"] for j in after if j["JobId"] not in before]
            if not new_jobs:
                raise errors.RenderError(
                    f"AddRenderJob produced no new entry for {item.name!r}.",
                    cause="Resolve refused the render — see state for current format/codec.",
                    state={
                        "clip_name": item.name,
                        "format_codec": self.current_format_and_codec(),
                    },
                )
            jobs.append(RenderJob(self, new_jobs[0]))

        if start:
            ids = [j.id for j in jobs]
            ok_start = self._project_raw.StartRendering(ids, False)
            if ok_start is False:
                raise errors.RenderError(
                    "Could not start per-clip render.",
                    cause="StartRendering returned False.",
                    state={"job_ids": ids},
                )

        return jobs

    def submit_and_wait(
        self,
        *,
        target_dir: str,
        custom_name: str | None = None,
        preset: str | None = None,
        format: str | None = None,
        codec: str | None = None,
        settings: dict[str, Any] | None = None,
        poll_interval: float = 1.0,
        timeout: float | None = None,
        stall_seconds: float = _DEFAULT_STALL_SECONDS,
    ) -> str:
        """Submit a render of the current timeline, block until done, return its output path.

        Combines :meth:`submit` (with ``start=True``) and
        :meth:`RenderJob.wait`. Used as a one-liner from build scripts that
        produce a single artifact per Resolve session::

            output = r.render.submit_and_wait(
                target_dir="/Volumes/Out",
                custom_name="hero_v007",
                format="mov",
                codec="ProRes4444XQ",
            )

        Args:
            target_dir, custom_name, preset, format, codec, settings:
                See :meth:`submit`.
            poll_interval, timeout, stall_seconds:
                See :meth:`RenderJob.wait`.

        Returns:
            The absolute path to the rendered file as reported by
            Resolve's ``OutputFilename`` job property.

        Raises:
            RenderError / RenderJobError: on submit failure or render
                failure / cancel / stall / timeout.
        """
        job = self.submit(
            target_dir=target_dir,
            custom_name=custom_name,
            preset=preset,
            format=format,
            codec=codec,
            settings=settings,
            start=True,
        )
        job.wait(poll_interval=poll_interval, timeout=timeout, stall_seconds=stall_seconds)
        path = job.output_path
        if not path:
            raise errors.RenderError(
                f"Render job {job.id} reported complete but has no output path.",
                cause=(
                    "OutputFilename was empty in the queue entry — Resolve sometimes "
                    "drops the job from GetRenderJobList immediately after completion."
                ),
                fix="Use `submit()` + `RenderJob.wait()` and capture the path before it's evicted.",
                state={"job_id": job.id, "target_dir": target_dir},
            )
        return path

    def render_single_clip(
        self,
        item: TimelineItem,
        *,
        target_dir: str,
        custom_name: str | None = None,
        preset: str | None = None,
        format: str | None = None,
        codec: str | None = None,
        settings: dict[str, Any] | None = None,
        start: bool = True,
    ) -> RenderJob:
        """Convenience for "render exactly this one timeline item".

        Sets the timeline mark in/out around the item, queues a single
        job, and (by default) starts it. Equivalent to calling
        :meth:`submit_per_clip` with one item, but returns a single
        :class:`RenderJob`.
        """
        jobs = self.submit_per_clip(
            [item],
            target_dir=target_dir,
            naming_template=custom_name or "{clip_name}",
            preset=preset,
            format=format,
            codec=codec,
            settings=settings,
            start=start,
        )
        return jobs[0]

    # --- streaming watch --------------------------------------------------

    def watch(
        self,
        job_ids: list[str] | None = None,
        *,
        poll_interval: float = 1.0,
    ) -> Iterator[dict[str, Any]]:
        """Yield structured status events until all jobs finish.

        Each event is a dict::

            {"type": "progress", "job_id": "...", "percent": 47, "eta_s": 180}
            {"type": "complete", "job_id": "...", "output_path": "..."}
            {"type": "failed",   "job_id": "...", "error": "..."}
        """
        targets = job_ids or [j["JobId"] for j in self.queue()]
        finished: set[str] = set()

        while len(finished) < len(targets):
            for jid in targets:
                if jid in finished:
                    continue
                s = self._project_raw.GetRenderJobStatus(jid) or {}
                status = s.get("JobStatus", "Unknown")
                if status == "Complete":
                    finished.add(jid)
                    yield {
                        "type": "complete",
                        "job_id": jid,
                        "output_path": RenderJob(self, jid).output_path,
                        "time_s": float(s.get("TimeTakenToRenderInMs", 0)) / 1000.0,
                    }
                elif status in ("Failed", "Cancelled"):
                    finished.add(jid)
                    yield {
                        "type": "failed" if status == "Failed" else "cancelled",
                        "job_id": jid,
                        "error": s.get("Error"),
                    }
                else:
                    yield {
                        "type": "progress",
                        "job_id": jid,
                        "status": status,
                        "percent": float(s.get("CompletionPercentage", 0)),
                        "eta_s": (
                            float(s["EstimatedTimeRemainingInMs"]) / 1000.0
                            if "EstimatedTimeRemainingInMs" in s
                            else None
                        ),
                    }
            if len(finished) < len(targets):
                time.sleep(poll_interval)


__all__ = ["RenderJob", "RenderNamespace"]
