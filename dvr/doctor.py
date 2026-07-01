"""Environment diagnostics for the dvr <-> Resolve setup.

:func:`diagnose` answers "why can't dvr talk to Resolve?" without raising:
it reports where the scripting library is expected, whether it exists,
whether the Resolve process is running, and (optionally) whether a live
connection can actually be established.

Shared by ``dvr doctor`` (CLI) and the ``doctor`` MCP tool so both
surfaces give identical answers.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import errors
from .connection import platform_paths, resolve_process_running

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - generated at build time
    __version__ = "0.0.0+local"


def diagnose(
    *,
    probe: bool = False,
    auto_launch: bool = False,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Return a structured snapshot of the dvr <-> Resolve environment.

    By default this is a fast static probe (no connection attempt). Pass
    ``probe=True`` to additionally try a live connection — that may take
    several seconds while macOS LAN IPs are tried.

    Never raises: connection failures are reported inside the returned
    dict under ``connection_error``.
    """
    api_dir, lib_path = platform_paths()
    out: dict[str, Any] = {
        "dvr_version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "scripting_api_dir": api_dir,
        "scripting_lib_path": lib_path,
        "scripting_lib_present": os.path.exists(lib_path),
        "resolve_process_running": resolve_process_running(),
        "env": {
            "RESOLVE_SCRIPT_API": os.environ.get("RESOLVE_SCRIPT_API"),
            "RESOLVE_SCRIPT_LIB": os.environ.get("RESOLVE_SCRIPT_LIB"),
        },
    }
    if not probe:
        return out

    from .resolve import Resolve

    try:
        r = Resolve(auto_launch=auto_launch, timeout=timeout)
        out["connected"] = True
        out["resolve_version"] = r.app.version
        out["resolve_product"] = r.app.product
        current = r.project.current
        out["current_project"] = current.name if current is not None else None
    except errors.DvrError as exc:
        out["connected"] = False
        out["connection_error"] = exc.to_dict()
    except Exception as exc:  # boundary: fusionscript can raise anything
        out["connected"] = False
        out["connection_error"] = {"type": type(exc).__name__, "message": str(exc)}
    return out


__all__ = ["diagnose"]
