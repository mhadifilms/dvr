"""Verify that ``dvr.connect`` does NOT call pinghosts unless explicitly opted in.

``pinghosts('')`` returns any Resolve scripting host on the LAN — including
remote render nodes — so the old fallback could silently connect to a remote
Resolve when the local one wasn't running. Now gated behind
``discover_remote=True`` (or ``DVR_DISCOVER_REMOTE=1``).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

import dvr.connection as connection
from dvr import errors


class _FakeScriptModule:
    """Stand-in for the fusionscript handle returned by _load_fusionscript."""

    def __init__(self) -> None:
        self.scriptapp_calls: list[tuple] = []
        self.pinghosts_calls: int = 0

    def scriptapp(self, *args):
        self.scriptapp_calls.append(args)
        return None  # never connects

    def pinghosts(self, _arg=""):
        self.pinghosts_calls += 1
        return {}  # never finds anything


@pytest.fixture
def fake_dvr_script(monkeypatch):
    fake = _FakeScriptModule()
    monkeypatch.setattr(connection, "_load_fusionscript", lambda timeout=None: fake)
    monkeypatch.setattr(connection, "_ensure_environment", lambda: ("", ""))
    monkeypatch.setattr(connection, "_lan_ips", lambda: ["192.168.1.10"])
    monkeypatch.setattr(connection, "_resolve_running", lambda: False)
    monkeypatch.setattr(connection, "_launch_resolve", lambda: False)
    return fake


def test_connect_default_does_not_call_pinghosts(fake_dvr_script, monkeypatch):
    """Default behavior: pinghosts must not be invoked."""
    monkeypatch.delenv("DVR_DISCOVER_REMOTE", raising=False)
    with pytest.raises(errors.ConnectionError):
        connection.connect(auto_launch=False, timeout=0.5, call_timeout=0.1)
    assert fake_dvr_script.pinghosts_calls == 0


def test_connect_discover_remote_true_calls_pinghosts(fake_dvr_script, monkeypatch):
    """Explicit opt-in: pinghosts is called as a fallback."""
    monkeypatch.delenv("DVR_DISCOVER_REMOTE", raising=False)
    with pytest.raises(errors.ConnectionError):
        connection.connect(
            auto_launch=False, timeout=0.5, call_timeout=0.1, discover_remote=True
        )
    assert fake_dvr_script.pinghosts_calls >= 1


def test_connect_env_var_enables_pinghosts(fake_dvr_script, monkeypatch):
    """DVR_DISCOVER_REMOTE=1 in env enables pinghosts without code change."""
    monkeypatch.setenv("DVR_DISCOVER_REMOTE", "1")
    with pytest.raises(errors.ConnectionError):
        connection.connect(auto_launch=False, timeout=0.5, call_timeout=0.1)
    assert fake_dvr_script.pinghosts_calls >= 1


def test_connect_env_var_no_when_zero(fake_dvr_script, monkeypatch):
    """DVR_DISCOVER_REMOTE=0 leaves pinghosts disabled."""
    monkeypatch.setenv("DVR_DISCOVER_REMOTE", "0")
    with pytest.raises(errors.ConnectionError):
        connection.connect(auto_launch=False, timeout=0.5, call_timeout=0.1)
    assert fake_dvr_script.pinghosts_calls == 0
