"""LUT and DCTL file management in Resolve's LUT directory.

Resolve reads LUTs and DCTLs from a fixed directory tree, not from the
project. Nothing in the scripting API creates, lists, or validates those
files, so this module works on the filesystem directly and then leaves it to
``Project.RefreshLUTList`` (``dvr render refresh-luts``) to make new files
selectable inside Resolve.

Every path is resolved inside the LUT root. Traversal outside it is refused,
so a generated name can never write over something else on the machine.
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Any

from . import errors, sandbox

#: Extensions Resolve loads as LUTs.
LUT_SUFFIXES = (".3dl", ".cube", ".dat", ".lut", ".olut")

#: Subdirectory that `dvr` writes generated files into, so they are easy to
#: tell apart from vendor LUTs shipped with Resolve.
GENERATED_SUBDIR = "dvr"

_DEFAULT_ROOTS = {
    "Darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",
    "Windows": r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT",
    "Linux": "/opt/resolve/LUT",
}

#: A DCTL must expose Resolve's per-pixel entry point.
_TRANSFORM_RE = re.compile(r"__DEVICE__\s+float3\s+transform\s*\(", re.MULTILINE)


def lut_root() -> Path:
    """Return Resolve's LUT directory.

    ``DVR_LUT_DIR`` overrides the platform default, which matters on Linux
    (where Resolve's install prefix varies) and for tests.
    """
    override = os.environ.get("DVR_LUT_DIR")
    if override:
        return Path(override).expanduser()
    system = platform.system()
    default = _DEFAULT_ROOTS.get(system)
    if default is None:
        raise errors.DvrError(
            f"No known DaVinci Resolve LUT directory for platform {system!r}.",
            fix="Set DVR_LUT_DIR to the LUT folder Resolve reads.",
        )
    return Path(default)


def resolve_path(path: str, *, must_exist: bool = False) -> Path:
    """Resolve ``path`` inside the LUT root, refusing to escape it."""
    root = lut_root()
    candidate = (root / path).expanduser()
    try:
        final = candidate.resolve()
        base = root.resolve()
    except OSError as exc:  # pragma: no cover - unreadable mount
        raise errors.DvrError(
            f"Could not resolve {path!r} inside the LUT directory.",
            cause=str(exc),
            state={"lut_root": str(root)},
        ) from exc
    if final != base and base not in final.parents:
        raise errors.DvrError(
            f"Refusing to use {path!r}: it resolves outside the LUT directory.",
            cause=f"{final} is not inside {base}.",
            fix="Pass a path relative to the LUT directory, without `..`.",
            state={"lut_root": str(base), "resolved": str(final)},
        )
    if must_exist and not final.is_file():
        raise errors.DvrError(
            f"No such file in the LUT directory: {path!r}.",
            fix="Call `lut_list` or `dctl_list` to see what is there.",
            state={"lut_root": str(base)},
        )
    return final


def relative_to_root(path: Path) -> str:
    """Return ``path`` relative to the LUT root, always with forward slashes.

    These strings are handed to agents and fed back into :func:`resolve_path`,
    so they must look the same on every platform. Windows accepts forward
    slashes, so a POSIX-style relative path round-trips everywhere.
    """
    return path.relative_to(lut_root()).as_posix()


def _listing(suffixes: tuple[str, ...], subdir: str | None) -> list[dict[str, Any]]:
    root = lut_root()
    base = resolve_path(subdir) if subdir else root
    if not base.is_dir():
        return []
    found = [p for p in sorted(base.rglob("*")) if p.is_file() and p.suffix.casefold() in suffixes]
    return [
        {
            "path": relative_to_root(p),
            "name": p.name,
            "bytes": p.stat().st_size,
        }
        for p in found
    ]


def list_luts(subdir: str | None = None) -> list[dict[str, Any]]:
    """List LUT files under the LUT root (recursively)."""
    return _listing(LUT_SUFFIXES, subdir)


def list_dctls(subdir: str | None = None) -> list[dict[str, Any]]:
    """List DCTL files under the LUT root (recursively)."""
    return _listing((".dctl",), subdir)


def read_dctl(path: str) -> str:
    """Return the source of a DCTL file."""
    return resolve_path(path, must_exist=True).read_text(encoding="utf-8")


def validate_dctl(content: str) -> None:
    """Reject DCTL source that Resolve cannot load.

    This is a structural check, not a compile. `dvr` runs outside Resolve and
    has no access to its CUDA/Metal/OpenCL toolchain, so it verifies the entry
    point and bracket balance rather than claiming the code builds. Errors
    inside the function body still surface in Resolve's console.
    """
    if not content.strip():
        raise errors.DvrError(
            "DCTL source is empty.",
            fix="Provide a `__DEVICE__ float3 transform(...)` entry point.",
        )
    if not _TRANSFORM_RE.search(content):
        raise errors.DvrError(
            "DCTL source has no `__DEVICE__ float3 transform(...)` entry point.",
            cause="Resolve calls `transform` once per pixel; without it the DCTL never loads.",
            fix=(
                "Add e.g. `__DEVICE__ float3 transform(int p_Width, int p_Height, "
                "int p_X, int p_Y, float p_R, float p_G, float p_B) { ... }`."
            ),
        )
    for opener, closer, label in (("{", "}", "braces"), ("(", ")", "parentheses")):
        if content.count(opener) != content.count(closer):
            raise errors.DvrError(
                f"DCTL source has unbalanced {label}.",
                cause=f"{content.count(opener)} {opener!r} vs {content.count(closer)} {closer!r}.",
                fix="Balance the source before writing it.",
            )


def write_dctl(path: str, content: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Validate then write a DCTL file, creating parent folders as needed."""
    if not path.casefold().endswith(".dctl"):
        raise errors.DvrError(
            f"DCTL path must end in .dctl, got {path!r}.",
            fix="Rename the file, e.g. `dvr/cool.dctl`.",
        )
    validate_dctl(content)
    target = resolve_path(path)
    if target.exists() and not overwrite:
        raise errors.DvrError(
            f"{path!r} already exists in the LUT directory.",
            fix="Pass overwrite=true to replace it.",
            state={"path": str(target)},
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": relative_to_root(target), "bytes": target.stat().st_size}


def delete_file(path: str) -> dict[str, Any]:
    """Delete a LUT or DCTL file from the LUT directory."""
    target = resolve_path(path, must_exist=True)
    if target.suffix.casefold() not in (*LUT_SUFFIXES, ".dctl"):
        raise errors.DvrError(
            f"Refusing to delete {path!r}: not a LUT or DCTL file.",
            state={"suffix": target.suffix},
        )
    relative = relative_to_root(target)
    target.unlink()
    return {"deleted": relative}


def generate_cube(
    path: str,
    transform: str,
    *,
    size: int = 33,
    title: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a .cube 3D LUT by evaluating ``transform`` per lattice point.

    ``transform`` is a Python expression over ``r``, ``g`` and ``b`` (floats in
    [0, 1]) that returns an ``(r, g, b)`` tuple. It runs through
    :func:`dvr.sandbox.restricted_eval`, so it cannot import or touch the host.
    """
    if not path.casefold().endswith(".cube"):
        raise errors.DvrError(
            f"LUT path must end in .cube, got {path!r}.",
            fix="Rename the file, e.g. `dvr/warm.cube`.",
        )
    if not 2 <= size <= 129:
        raise errors.DvrError(
            f"Cube size must be between 2 and 129, got {size}.",
            fix="Use a common size: 17, 33, or 65.",
        )
    target = resolve_path(path)
    if target.exists() and not overwrite:
        raise errors.DvrError(
            f"{path!r} already exists in the LUT directory.",
            fix="Pass overwrite=true to replace it.",
            state={"path": str(target)},
        )

    denominator = size - 1
    lines = [f'TITLE "{title or target.stem}"', f"LUT_3D_SIZE {size}", ""]
    # .cube iterates red fastest, then green, then blue.
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                point = {
                    "r": ri / denominator,
                    "g": gi / denominator,
                    "b": bi / denominator,
                }
                value = sandbox.restricted_eval(transform, point)
                lines.append(_format_triplet(value, transform, point))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "path": relative_to_root(target),
        "size": size,
        "points": size**3,
        "bytes": target.stat().st_size,
    }


def _format_triplet(value: Any, transform: str, point: dict[str, float]) -> str:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise errors.DvrError(
            "LUT transform must return an (r, g, b) tuple of three numbers.",
            cause=f"Got {type(value).__name__} at r={point['r']:.4g} g={point['g']:.4g} b={point['b']:.4g}.",
            fix="Return a 3-tuple, e.g. `(r ** 0.8, g ** 0.8, b ** 0.8)`.",
            state={"transform": transform},
        )
    out = []
    for channel in value:
        if not isinstance(channel, (int, float)) or isinstance(channel, bool):
            raise errors.DvrError(
                "LUT transform returned a non-numeric channel.",
                fix="Return floats, e.g. `(r * 1.0, g * 1.0, b * 1.0)`.",
                state={"transform": transform, "value": repr(value)},
            )
        out.append(f"{min(max(float(channel), 0.0), 1.0):.6f}")
    return " ".join(out)


__all__ = [
    "GENERATED_SUBDIR",
    "LUT_SUFFIXES",
    "delete_file",
    "generate_cube",
    "list_dctls",
    "list_luts",
    "lut_root",
    "read_dctl",
    "relative_to_root",
    "resolve_path",
    "validate_dctl",
    "write_dctl",
]
