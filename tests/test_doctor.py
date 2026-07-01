"""Tests for the shared diagnostics module (dvr.doctor)."""

from __future__ import annotations

from dvr import doctor


def test_diagnose_static_returns_expected_keys() -> None:
    report = doctor.diagnose(probe=False)
    assert set(report) >= {
        "dvr_version",
        "python",
        "platform",
        "scripting_api_dir",
        "scripting_lib_path",
        "scripting_lib_present",
        "resolve_process_running",
        "env",
    }
    # Static mode must not attempt a connection.
    assert "connected" not in report


def test_diagnose_probe_never_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from dvr import errors

    class _BoomResolve:
        def __init__(self, **kwargs: object) -> None:
            raise errors.ConnectionError("nope", fix="install resolve")

    import dvr.resolve

    monkeypatch.setattr(dvr.resolve, "Resolve", _BoomResolve)
    report = doctor.diagnose(probe=True, auto_launch=False, timeout=0.1)
    assert report["connected"] is False
    assert report["connection_error"]["message"] == "nope"
