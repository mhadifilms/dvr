"""Structured output for the CLI.

Every command produces a Python value (usually a dict or list of dicts).
:func:`emit` decides how to render it based on:

* ``--format json|table|yaml``        explicit user choice
* ``DVR_FORMAT`` env var               persistent default
* whether stdout is a TTY              auto: ``table`` if interactive, ``json`` if piped

JSON is always one well-formed object per command (no trailing newlines,
no ANSI codes), so ``dvr ... | jq`` Just Works. Tables use ``rich`` and
are only printed when interactive.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

_console = Console()
_err_console = Console(stderr=True)


def resolve_format(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("DVR_FORMAT")
    if env:
        return env
    return "table" if sys.stdout.isatty() else "json"


def emit(data: Any, *, fmt: str | None = None, headline: str | None = None) -> None:
    """Render ``data`` to stdout in the chosen format."""
    chosen = resolve_format(fmt)
    if chosen == "json":
        sys.stdout.write(json.dumps(data, indent=2, default=_default_json) + "\n")
        return
    if chosen == "yaml":
        sys.stdout.write(yaml.safe_dump(_to_plain(data), sort_keys=False))
        return
    if chosen == "table":
        _emit_table(data, headline=headline)
        return
    raise ValueError(f"Unknown format: {chosen!r} (expected json|table|yaml)")


def emit_error(error: Any, *, fmt: str | None = None) -> None:
    """Render an error to stderr and exit with code 1.

    For ``json``, this writes a single object to stderr that mirrors the
    ``DvrError.to_dict`` schema.
    """
    chosen = resolve_format(fmt)
    if chosen == "json":
        if hasattr(error, "to_dict"):
            payload = error.to_dict()
        else:
            payload = {"type": type(error).__name__, "message": str(error)}
        _err_console.file.write(json.dumps(payload, indent=2, default=_default_json) + "\n")
    else:
        _err_console.print(f"[bold red]error:[/bold red] {error}")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _default_json(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "inspect"):
        return value.inspect()
    return str(value)


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if hasattr(value, "to_dict"):
        return _to_plain(value.to_dict())
    if hasattr(value, "inspect"):
        return _to_plain(value.inspect())
    return value


def _emit_table(data: Any, *, headline: str | None) -> None:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        _print_dict_list_as_table(data, headline=headline)
        return
    if isinstance(data, dict):
        _print_dict_as_kv_table(data, headline=headline)
        return
    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        for item in data:
            _console.print(item)
        return
    if data is None:
        return
    _console.print(data)


def _print_dict_list_as_table(rows: list[dict[str, Any]], *, headline: str | None) -> None:
    columns = list(rows[0].keys())
    table = Table(title=headline, show_lines=False)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[_format_cell(row.get(c)) for c in columns])
    _console.print(table)


def _print_dict_as_kv_table(d: dict[str, Any], *, headline: str | None) -> None:
    table = Table(title=headline, show_header=False)
    table.add_column("key", style="cyan", no_wrap=True)
    table.add_column("value")
    for key, value in d.items():
        table.add_row(str(key), _format_cell(value))
    _console.print(table)


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=_default_json)
    return str(value)


__all__ = ["emit", "emit_error", "resolve_format"]
