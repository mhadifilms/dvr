"""Tests for the lint report serialization (no Resolve required)."""

from __future__ import annotations

from dvr import lint


def test_lint_report_classifies_issues() -> None:
    report = lint.LintReport(
        issues=[
            lint.Issue(severity="error", code="e1", message="boom"),
            lint.Issue(severity="warning", code="w1", message="hmm"),
            lint.Issue(severity="info", code="i1", message="fyi"),
        ]
    )
    assert len(report.errors) == 1
    assert len(report.warnings) == 1
    assert len(report.infos) == 1
    assert not report.ok


def test_lint_report_ok_when_no_errors_or_warnings() -> None:
    report = lint.LintReport(issues=[lint.Issue(severity="info", code="i1", message="fyi")])
    assert report.ok


def test_lint_report_to_dict() -> None:
    report = lint.LintReport(issues=[lint.Issue(severity="error", code="e1", message="boom")])
    payload = report.to_dict()
    assert payload["summary"]["errors"] == 1
    assert payload["summary"]["ok"] is False
    assert payload["issues"][0]["code"] == "e1"
