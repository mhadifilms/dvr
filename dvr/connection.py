"""Connection to a running DaVinci Resolve instance.

This module owns every concern that makes "just import the API" hard:

* Discovering the platform-specific scripting library and module path.
* Loading ``fusionscript.so`` / ``fusionscript.dll`` directly to avoid
  the import-time hangs ``DaVinciResolveScript.py`` is prone to.
* Working around the macOS quirk where Resolve binds the scripting
  socket to the machine's LAN IP rather than ``127.0.0.1``.
* Wrapping every API call in a thread-with-timeout so a single
  unresponsive Resolve process can't deadlock the caller.
* Auto-launching Resolve when not running, and waiting for it to
  become reachable.

Public surface:

    connect(auto_launch=True, timeout=30.0) -> ResolveHandle

Everything else is private and prefixed with ``_``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import errors

logger = logging.getLogger("dvr.connection")


# ---------------------------------------------------------------------------
# Platform paths
# ---------------------------------------------------------------------------

_MAC_SCRIPT_API = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/"
)
_MAC_SCRIPT_LIB = (
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
)

_LINUX_SCRIPT_API = "/opt/resolve/Developer/Scripting/"
_LINUX_SCRIPT_LIB = "/opt/resolve/libs/Fusion/fusionscript.so"


def _platform_paths() -> tuple[str, str]:
    """Return ``(script_api_dir, script_lib_path)`` for the current platform."""
    if sys.platform == "darwin":
        return _MAC_SCRIPT_API, _MAC_SCRIPT_LIB
    if sys.platform == "win32":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        api = os.path.join(
            program_data,
            "Blackmagic Design",
            "DaVinci Resolve",
            "Support",
            "Developer",
            "Scripting",
        )
        lib = os.path.join(
            program_files,
            "Blackmagic Design",
            "DaVinci Resolve",
            "fusionscript.dll",
        )
        return api, lib
    return _LINUX_SCRIPT_API, _LINUX_SCRIPT_LIB


def _ensure_environment() -> tuple[str, str]:
    """Set ``RESOLVE_SCRIPT_API`` / ``RESOLVE_SCRIPT_LIB`` if unset; return them."""
    api_default, lib_default = _platform_paths()
    api = os.environ.setdefault("RESOLVE_SCRIPT_API", api_default)
    lib = os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib_default)
    modules = os.path.join(api, "Modules")
    if modules not in sys.path:
        sys.path.insert(0, modules)
    return api, lib


# ---------------------------------------------------------------------------
# Timeout-wrapped calls
# ---------------------------------------------------------------------------


def _call_with_timeout(
    func: Callable[[], Any],
    timeout: float,
    label: str,
) -> Any:
    """Run ``func()`` on a background thread; return ``None`` on timeout/error."""
    result: list[Any] = [None]
    error: list[BaseException | None] = [None]

    def runner() -> None:
        try:
            result[0] = func()
        except BaseException as exc:
            error[0] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        logger.warning("%s: timed out after %.1fs", label, timeout)
        return None
    if error[0] is not None:
        logger.warning("%s: %s", label, error[0])
        return None
    return result[0]


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------


def _load_fusionscript(timeout: float = 10.0) -> Any:
    """Load ``fusionscript.so``/``.dll`` directly, with a timeout.

    Importing ``DaVinciResolveScript`` triggers ``fusionscript`` loading,
    which can block forever if Resolve's IPC is unresponsive. Loading the
    extension directly via ``importlib.machinery.ExtensionFileLoader``
    keeps us in control.
    """
    import importlib.machinery
    import importlib.util

    _, lib_path = _platform_paths()
    lib_path = os.environ.get("RESOLVE_SCRIPT_LIB", lib_path)

    if not Path(lib_path).exists():
        raise errors.NotInstalledError(
            "Could not find Resolve's scripting library.",
            cause=f"Expected at {lib_path} but the file does not exist.",
            fix="Install DaVinci Resolve, or set RESOLVE_SCRIPT_LIB to the correct path.",
            state={"platform": sys.platform, "expected_path": lib_path},
        )

    def _load() -> Any:
        loader = importlib.machinery.ExtensionFileLoader("fusionscript", lib_path)
        spec = importlib.util.spec_from_loader("fusionscript", loader)
        if spec is None:
            raise RuntimeError("Could not build module spec for fusionscript")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    module = _call_with_timeout(_load, timeout=timeout, label="load fusionscript")
    if module is None:
        raise errors.ConnectionError(
            "Failed to load Resolve's scripting library.",
            cause="The fusionscript module took too long to load or raised an exception.",
            fix="Quit and relaunch DaVinci Resolve, then retry.",
            state={"lib_path": lib_path, "timeout_s": timeout},
        )
    return module


# ---------------------------------------------------------------------------
# Resolve discovery (macOS LAN-IP, pinghosts)
# ---------------------------------------------------------------------------


def _lan_ips() -> list[str]:
    """Return non-loopback, non-link-local IPv4 addresses for this machine."""
    try:
        result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    ips: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("inet ") and "127.0.0.1" not in line and "169.254." not in line:
            parts = line.split()
            if len(parts) >= 2:
                ips.append(parts[1])
    return ips


def _scriptapp_at(dvr_script: Any, ip: str, timeout: float, label: str) -> Any:
    """Run ``scriptapp("Resolve", ip)`` with a timeout."""

    def call() -> Any:
        return dvr_script.scriptapp("Resolve", ip)

    return _call_with_timeout(call, timeout=timeout, label=label)


def _try_lan_ip(dvr_script: Any, timeout: float) -> Any:
    """Try connecting via each of the machine's LAN IPs (macOS workaround)."""
    for ip in _lan_ips():
        logger.debug("trying Resolve at LAN IP %s", ip)
        handle = _scriptapp_at(dvr_script, ip, timeout, f"scriptapp({ip})")
        if handle is not None:
            logger.info("connected to Resolve via %s", ip)
            return handle
    return None


def _try_pinghosts(dvr_script: Any, timeout: float) -> Any:
    """Use ``pinghosts('')`` to discover the Resolve host, then connect."""

    def call_pinghosts() -> Any:
        return dvr_script.pinghosts("")

    hosts = _call_with_timeout(call_pinghosts, timeout=timeout, label="pinghosts")
    if not hosts:
        return None
    for info in hosts.values():
        ip = info.get("IP", "")
        host_dict = info.get("Hosts", {})
        if ip and "Resolve" in str(host_dict):
            logger.debug("pinghosts found Resolve at %s", ip)
            handle = _scriptapp_at(dvr_script, ip, timeout, f"scriptapp({ip}) [pinghosts]")
            if handle is not None:
                return handle
    return None


# ---------------------------------------------------------------------------
# Auto-launch
# ---------------------------------------------------------------------------


def _resolve_running() -> bool:
    """Best-effort check: is the Resolve process running?"""
    try:
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            name = "Resolve" if sys.platform == "darwin" else "resolve"
            result = subprocess.run(
                ["pgrep", "-x", name], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Resolve.exe"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return "Resolve.exe" in result.stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return False


def _launch_resolve() -> bool:
    """Launch Resolve if it isn't running. Returns True if a launch was attempted."""
    if _resolve_running():
        return True
    logger.info("launching DaVinci Resolve...")
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", "DaVinci Resolve"], capture_output=True, timeout=10)
            return True
        if sys.platform == "win32":
            for candidate in (
                r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe",
                r"C:\Program Files (x86)\Blackmagic Design\DaVinci Resolve\Resolve.exe",
            ):
                if os.path.exists(candidate):
                    subprocess.Popen([candidate], start_new_session=True)
                    return True
        if sys.platform.startswith("linux"):
            for candidate in ("/opt/resolve/bin/resolve", "/usr/bin/resolve"):
                if os.path.exists(candidate):
                    subprocess.Popen([candidate], start_new_session=True)
                    return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("launch failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def connect(
    *,
    auto_launch: bool = True,
    timeout: float = 30.0,
    call_timeout: float = 5.0,
    discover_remote: bool | None = None,
) -> Any:
    """Connect to DaVinci Resolve and return its scripting handle.

    By default this only talks to **the local machine's Resolve**. The
    ``pinghosts`` network-discovery fallback is gated behind
    ``discover_remote=True`` (or the ``DVR_DISCOVER_REMOTE=1`` env var)
    because ``pinghosts`` will silently connect to *any* Resolve scripting
    host on the LAN — e.g. a render node — when the local Resolve isn't
    running. Surprising and easy to miss.

    The macOS LAN-IP workaround (``_try_lan_ip``) is unaffected: it iterates
    *this* machine's network interfaces only, which is the documented
    workaround for Resolve binding its scripting socket to a LAN IP rather
    than 127.0.0.1.

    Args:
        auto_launch:     If True, launch Resolve when it isn't running.
        timeout:         Total seconds to wait for a connection (incl. launch).
        call_timeout:    Per-call timeout for the underlying scripting calls
                         (``scriptapp``, ``pinghosts``).
        discover_remote: If True, fall back to ``pinghosts('')`` when no
                         local Resolve answers. Defaults to ``False`` (or
                         the value of ``$DVR_DISCOVER_REMOTE``). Pass
                         explicitly when you intentionally want to drive a
                         remote Resolve over the LAN.

    Returns:
        The Resolve handle (the same object as ``DaVinciResolveScript.scriptapp("Resolve")``).

    Raises:
        ConnectionError:        Could not reach Resolve within the timeout.
        NotInstalledError:      Resolve's scripting library was not found.
        ScriptingDisabledError: Resolve is running but external scripting is off.
    """
    if discover_remote is None:
        discover_remote = os.environ.get("DVR_DISCOVER_REMOTE", "").strip() in ("1", "true", "yes")

    _ensure_environment()
    dvr_script = _load_fusionscript(timeout=min(call_timeout * 2, 10.0))

    deadline = time.monotonic() + timeout
    launched = False

    while time.monotonic() < deadline:
        # macOS: LAN IP first; otherwise try localhost first.
        if sys.platform == "darwin":
            handle = _try_lan_ip(dvr_script, timeout=call_timeout)
            if handle is None and discover_remote:
                handle = _try_pinghosts(dvr_script, timeout=call_timeout)
        else:

            def call_local() -> Any:
                return dvr_script.scriptapp("Resolve")

            handle = _call_with_timeout(
                call_local, timeout=call_timeout, label="scriptapp(localhost)"
            )
            if handle is None:
                handle = _try_lan_ip(dvr_script, timeout=call_timeout)
            if handle is None and discover_remote:
                handle = _try_pinghosts(dvr_script, timeout=call_timeout)

        if handle is not None:
            try:
                version = handle.GetVersionString()
                logger.info("connected to DaVinci Resolve %s", version)
            except Exception:
                pass
            return handle

        if not auto_launch:
            break

        if not launched:
            if not _resolve_running():
                _launch_resolve()
            launched = True

        time.sleep(1.0)

    # Build a useful diagnosis instead of "connection failed".
    running = _resolve_running()
    if running:
        raise errors.ScriptingDisabledError(
            "Could not reach DaVinci Resolve over its scripting socket.",
            cause=(
                "Resolve is running but did not respond to scriptapp() within the timeout."
            ),
            fix=(
                "In Resolve, open Preferences > General and set "
                "'External scripting using' to 'Local'. Then quit and relaunch Resolve."
            ),
            state={
                "platform": sys.platform,
                "timeout_s": timeout,
                "auto_launch": auto_launch,
                "discover_remote": discover_remote,
            },
        )
    raise errors.ConnectionError(
        "DaVinci Resolve is not running locally and could not be launched.",
        cause="No local Resolve responded; remote network discovery is disabled by default.",
        fix=(
            "Launch DaVinci Resolve on this machine and retry. "
            "To intentionally drive a remote Resolve over the LAN, pass "
            "discover_remote=True or set DVR_DISCOVER_REMOTE=1."
        ),
        state={
            "platform": sys.platform,
            "timeout_s": timeout,
            "auto_launch": auto_launch,
            "discover_remote": discover_remote,
        },
    )


__all__ = ["connect"]
