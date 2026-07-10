"""Tests for the dvr↔prpr parity contract (no Resolve required)."""

from __future__ import annotations

from dvr import errors, schema

VALID_STATUSES = {"both", "dvr-only", "prpr-only"}


def test_not_supported_error_exists_and_inherits_dvr_error() -> None:
    assert issubclass(errors.NotSupportedError, errors.DvrError)
    assert "NotSupportedError" in errors.__all__


def test_not_supported_error_serializes() -> None:
    err = errors.NotSupportedError(
        "effects.apply is not supported in Resolve",
        cause="Resolve's scripting API has no effect factory",
        fix="use prpr, or apply the effect manually in the Edit page",
        state={"operation": "effects.apply"},
    )
    payload = err.to_dict()
    assert payload["type"] == "NotSupportedError"
    assert payload["message"] == "effects.apply is not supported in Resolve"
    assert payload["state"] == {"operation": "effects.apply"}
    assert "Fix:" in str(err)


def test_parity_topic_resolves_statically() -> None:
    catalog = schema.get_topic("parity")
    operations = catalog["operations"]
    assert operations is schema.PARITY
    assert catalog["statuses"] == ["both", "dvr-only", "prpr-only"]
    assert operations["render.queue"]["status"] == "dvr-only"
    assert operations["effects.apply"]["status"] == "prpr-only"
    assert operations["timeline.inspect"]["status"] == "both"


def test_parity_topic_listed() -> None:
    assert "parity" in schema.TOPICS


def test_parity_statuses_all_valid() -> None:
    for op, entry in schema.PARITY.items():
        assert entry["status"] in VALID_STATUSES, f"{op}: invalid status {entry['status']!r}"


def test_parity_dvr_only_entries_carry_reasons() -> None:
    for op, entry in schema.PARITY.items():
        if entry["status"] == "dvr-only":
            assert entry.get("reason"), f"{op}: dvr-only without a reason"
