"""Verify ``dvr.connect`` falls back to localhost ``scriptapp("Resolve")``.

On macOS the connect path tries the machine's LAN IPs first (the documented
quirk where Resolve binds its scripting socket to a LAN IP), but it must also
fall back to plain ``scriptapp("Resolve")`` on localhost. Otherwise a running,
scriptable local Resolve that binds to 127.0.0.1 (External scripting = Local),
or a machine with no LAN IP, is unreachable without remote discovery.
"""
from __future__ import annotations

import pytest

import dvr.connection as connection


class _FakeScriptModule:
    """Records scriptapp/pinghosts calls; localhost can be made to succeed."""

    def __init__(self, *, localhost_succeeds: bool = False) -> None:
        self.scriptapp_calls: list[tuple] = []
        self.pinghosts_calls: int = 0
        self._localhost_succeeds = localhost_succeeds

    def scriptapp(self, *args):
        self.scriptapp_calls.append(args)
        # A localhost connect is scriptapp("Resolve") with no IP argument.
        if self._localhost_succeeds and len(args) == 1:
            return _FakeHandle()
        return None

    def pinghosts(self, _arg=""):
        self.pinghosts_calls += 1
        return {}


class _FakeHandle:
    def GetVersionString(self) -> str:
        return "20.3.1"


def _install(monkeypatch, fake, *, lan_ips):
    monkeypatch.setattr(connection, "_load_fusionscript", lambda timeout=None: fake)
    monkeypatch.setattr(connection, "_ensure_environment", lambda: ("", ""))
    monkeypatch.setattr(connection, "_lan_ips", lambda: lan_ips)
    monkeypatch.setattr(connection, "_resolve_running", lambda: False)
    monkeypatch.setattr(connection, "_launch_resolve", lambda: False)
    monkeypatch.setattr(connection.sys, "platform", "darwin")
    monkeypatch.delenv("DVR_DISCOVER_REMOTE", raising=False)


def test_macos_attempts_localhost_after_lan_ip(monkeypatch):
    """When LAN IPs fail, localhost scriptapp("Resolve") is still attempted."""
    fake = _FakeScriptModule(localhost_succeeds=False)
    _install(monkeypatch, fake, lan_ips=["192.168.1.10"])

    with pytest.raises(connection.errors.ConnectionError):
        connection.connect(auto_launch=False, timeout=0.5, call_timeout=0.1)

    # LAN IP tried first, then a bare localhost call with no IP argument.
    assert ("Resolve", "192.168.1.10") in fake.scriptapp_calls
    assert ("Resolve",) in fake.scriptapp_calls
    # No remote discovery by default.
    assert fake.pinghosts_calls == 0


def test_macos_localhost_only_connects(monkeypatch):
    """A local Resolve bound to 127.0.0.1 connects without remote discovery."""
    fake = _FakeScriptModule(localhost_succeeds=True)
    _install(monkeypatch, fake, lan_ips=["192.168.1.10"])

    handle = connection.connect(auto_launch=False, timeout=0.5, call_timeout=0.1)

    assert isinstance(handle, _FakeHandle)
    assert ("Resolve",) in fake.scriptapp_calls
    assert fake.pinghosts_calls == 0


def test_macos_no_lan_ip_falls_back_to_localhost(monkeypatch):
    """With no LAN IP at all, localhost is still attempted and connects."""
    fake = _FakeScriptModule(localhost_succeeds=True)
    _install(monkeypatch, fake, lan_ips=[])

    handle = connection.connect(auto_launch=False, timeout=0.5, call_timeout=0.1)

    assert isinstance(handle, _FakeHandle)
    assert ("Resolve",) in fake.scriptapp_calls
