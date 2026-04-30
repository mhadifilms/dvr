"""Pre-flight validation for projects, timelines, and renders.

Catches the kinds of problems that crash a render or invalidate an
export — *before* you start them. The Resolve UI will let you queue a
render with offline media, mismatched FPS, or no current timeline; the
linter says no, here, look.

Each rule returns zero or more :class:`Issue` records. The :func:`lint`
entry point runs every rule and returns a structured report.

Severities:

* **error**   — will almost certainly cause downstream failure
* **warning** — likely problem; review before committing
* **info**    — observational; no action required
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .resolve import Resolve


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    target: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LintReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "infos": len(self.infos),
                "ok": self.ok,
            },
            "issues": [i.to_dict() for i in self.issues],
        }


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


Rule = Callable[["Resolve"], list[Issue]]


def _rule_project_loaded(r: Resolve) -> list[Issue]:
    if r.project.current is None:
        return [
            Issue(
                severity="error",
                code="no_project",
                message="No project is currently loaded.",
                detail={"fix": "Load or create a project."},
            )
        ]
    return []


def _rule_timeline_loaded(r: Resolve) -> list[Issue]:
    project = r.project.current
    if project is None or project.timeline.current is not None:
        return []
    return [
        Issue(
            severity="warning",
            code="no_current_timeline",
            message=f"Project {project.name!r} has no current timeline.",
            target=f"project:{project.name}",
            detail={"fix": "Switch to or create a timeline."},
        )
    ]


def _rule_timeline_fps_consistent(r: Resolve) -> list[Issue]:
    project = r.project.current
    if project is None:
        return []
    issues: list[Issue] = []
    fpses: dict[float, list[str]] = {}
    for tl in project.timeline.list():
        fpses.setdefault(tl.fps, []).append(tl.name)
    if len(fpses) > 1:
        issues.append(
            Issue(
                severity="info",
                code="mixed_fps",
                message=f"Project has timelines at {len(fpses)} different frame rates.",
                target=f"project:{project.name}",
                detail={"by_fps": {str(k): v for k, v in fpses.items()}},
            )
        )
    return issues


def _rule_timeline_has_clips(r: Resolve) -> list[Issue]:
    project = r.project.current
    if project is None:
        return []
    issues: list[Issue] = []
    for tl in project.timeline.list():
        info = tl.inspect()
        video_clip_count = sum(
            int(t.get("item_count", t.get("clip_count", 0)))
            for t in info["tracks"].get("video", [])
        )
        if video_clip_count == 0:
            issues.append(
                Issue(
                    severity="warning",
                    code="empty_timeline",
                    message=f"Timeline {tl.name!r} has no video clips.",
                    target=f"timeline:{tl.name}",
                )
            )
    return issues


def _rule_render_format_codec(r: Resolve) -> list[Issue]:
    if r.project.current is None:
        return []
    try:
        current = r.render.current_format_and_codec()
    except Exception:
        return []
    if not current.get("format") or not current.get("codec"):
        return [
            Issue(
                severity="warning",
                code="render_format_unset",
                message="Render format/codec is not configured.",
                detail={
                    "current": current,
                    "fix": "Run `dvr render formats` / `dvr render codecs <fmt>` and "
                    "submit with --format/--codec or load a preset.",
                },
            )
        ]
    return []


def _rule_color_science_set(r: Resolve) -> list[Issue]:
    project = r.project.current
    if project is None:
        return []
    mode = project.get_setting("colorScienceMode")
    if not mode:
        return [
            Issue(
                severity="info",
                code="color_science_unset",
                message="Project color science mode is not explicitly set.",
                target=f"project:{project.name}",
                detail={
                    "fix": "Set via `dvr apply` with a color preset (e.g. rec709_gamma24).",
                },
            )
        ]
    return []


_DEFAULT_RULES: tuple[Rule, ...] = (
    _rule_project_loaded,
    _rule_timeline_loaded,
    _rule_timeline_fps_consistent,
    _rule_timeline_has_clips,
    _rule_render_format_codec,
    _rule_color_science_set,
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lint(resolve: Resolve, *, rules: tuple[Rule, ...] | None = None) -> LintReport:
    """Run every rule and return a :class:`LintReport`."""
    issues: list[Issue] = []
    for rule in rules or _DEFAULT_RULES:
        try:
            issues.extend(rule(resolve))
        except Exception as exc:
            issues.append(
                Issue(
                    severity="warning",
                    code="rule_failure",
                    message=f"Rule {rule.__name__} raised: {exc}",
                    detail={"rule": rule.__name__},
                )
            )
    return LintReport(issues=issues)


__all__ = ["Issue", "LintReport", "Rule", "lint"]
