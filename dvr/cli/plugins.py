"""CLI plugin discovery and registration.

Plugins extend the ``dvr`` CLI with extra sub-apps. There are two
discovery mechanisms:

1. **Entry points (preferred, for installed packages).** A plugin
   declares an entry point in the ``dvr.plugins`` group. The value
   points at a ``register(app)`` callable (or a ``typer.Typer`` instance)
   whose argument is the root ``dvr`` Typer ``app``::

       # in your plugin's pyproject.toml
       [project.entry-points."dvr.plugins"]
       myshow = "myshow.cli:plugin"

       # in myshow/cli.py
       import typer

       plugin = typer.Typer(help="MyShow workflow commands")

       @plugin.command()
       def build():
           ...

   Once installed (``pip install myshow``), ``dvr myshow build`` is a
   first-class subcommand.

2. **User-managed (for local repos, dev work, or non-packaged dirs).**
   A user config file under ``~/.config/dvr/plugins.toml`` lists
   directories or modules to load at CLI startup::

       [[plugin]]
       name = "myshow"
       path = "/Users/me/work/myshow"  # added to sys.path; "myshow.cli:plugin" loaded

   Manage entries with ``dvr plugin add``, ``dvr plugin remove``,
   ``dvr plugin list``.

A plugin's exported value can be:

* A ``typer.Typer`` instance — added under ``dvr <name>``.
* A ``register(app)`` callable — invoked with the root app, free to add
  sub-apps and commands itself.
"""

from __future__ import annotations

import importlib
import importlib.metadata as _md
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer

logger = logging.getLogger("dvr.plugins")

ENTRY_POINT_GROUP = "dvr.plugins"


def _config_path() -> Path:
    """Per-user plugin manifest path. Honours ``$XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "dvr" / "plugins.toml"


def _read_user_plugins() -> list[dict[str, str]]:
    path = _config_path()
    if not path.exists():
        return []
    try:
        import tomllib  # type: ignore[unused-ignore, import-not-found]
    except ImportError:  # pragma: no cover — Python <3.11 fallback
        import tomli as tomllib  # type: ignore[no-redef, unused-ignore, import-not-found]
    try:
        data = tomllib.loads(path.read_text())
    except Exception as exc:
        logger.warning("could not parse %s: %s", path, exc)
        return []
    plugin_entries = data.get("plugin")
    if isinstance(plugin_entries, dict):
        return [plugin_entries]
    if isinstance(plugin_entries, list):
        return [p for p in plugin_entries if isinstance(p, dict)]
    return []


def _write_user_plugins(entries: list[dict[str, str]]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for entry in entries:
        lines.append("[[plugin]]")
        for key, value in entry.items():
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n")


def _resolve_target(spec: str) -> Any:
    """Resolve a ``module:attr`` (or just ``module``) spec to a Python object."""
    if ":" in spec:
        mod_name, attr = spec.split(":", 1)
    else:
        mod_name, attr = spec, "plugin"
    module = importlib.import_module(mod_name)
    return getattr(module, attr) if attr else module


def _attach(app: typer.Typer, name: str, target: Any) -> bool:
    """Attach a plugin to the root app. Returns True on success."""
    if isinstance(target, typer.Typer):
        app.add_typer(target, name=name)
        return True
    if callable(target):
        try:
            target(app)
            return True
        except Exception as exc:
            logger.warning("plugin %r register() raised: %s", name, exc)
            return False
    logger.warning(
        "plugin %r exports neither a Typer nor a register(app) callable",
        name,
    )
    return False


def _iter_entry_point_plugins() -> Iterator[tuple[str, Any]]:
    try:
        eps: Any = _md.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover — older importlib.metadata
        eps = _md.entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[arg-type, attr-defined, unused-ignore]
    for ep in eps:
        try:
            yield ep.name, ep.load()
        except Exception as exc:
            logger.warning("plugin %r failed to load: %s", ep.name, exc)


def _iter_user_plugins() -> Iterator[tuple[str, Any]]:
    for entry in _read_user_plugins():
        name = entry.get("name") or entry.get("module") or "<unknown>"
        path = entry.get("path")
        spec = entry.get("module") or f"{name}.cli:plugin"
        if path and Path(path).exists() and path not in sys.path:
            sys.path.insert(0, path)
        try:
            yield name, _resolve_target(spec)
        except Exception as exc:
            logger.warning("user plugin %r failed: %s", name, exc)


def load_plugins(app: typer.Typer) -> list[str]:
    """Discover and attach all plugins to ``app``. Returns names of attached plugins."""
    attached: list[str] = []
    seen: set[str] = set()
    for name, target in _iter_entry_point_plugins():
        if name in seen:
            continue
        seen.add(name)
        if _attach(app, name, target):
            attached.append(name)
    for name, target in _iter_user_plugins():
        if name in seen:
            continue
        seen.add(name)
        if _attach(app, name, target):
            attached.append(name)
    return attached


# ---------------------------------------------------------------------------
# `dvr plugin` sub-app
# ---------------------------------------------------------------------------


plugin_app = typer.Typer(
    name="plugin",
    help="Manage user-installed CLI plugins (~/.config/dvr/plugins.toml).",
    no_args_is_help=True,
)


@plugin_app.command("list")
def list_plugins() -> None:
    """Print every registered plugin (entry-point + user-managed)."""
    rows: list[dict[str, str]] = []
    for name, _ in _iter_entry_point_plugins():
        rows.append({"name": name, "source": "entry_point"})
    for entry in _read_user_plugins():
        rows.append(
            {
                "name": entry.get("name", "<unnamed>"),
                "source": "user",
                "path": entry.get("path", ""),
                "module": entry.get("module", ""),
            }
        )
    if not rows:
        typer.echo("no plugins registered")
        return
    for row in rows:
        bits = [f"{row['name']:<20}", row["source"]]
        if row.get("path"):
            bits.append(row["path"])
        if row.get("module"):
            bits.append(row["module"])
        typer.echo("  ".join(bits))


@plugin_app.command("add")
def add_plugin(
    name: str = typer.Argument(..., help="Subcommand name (becomes `dvr <name>`)."),
    path_or_module: str = typer.Argument(
        ...,
        help=(
            "Either a directory path containing the plugin package, or a "
            "module spec in 'pkg.cli:plugin' form."
        ),
    ),
) -> None:
    """Register a local plugin under ``~/.config/dvr/plugins.toml``."""
    entries = _read_user_plugins()
    entries = [e for e in entries if e.get("name") != name]

    candidate = Path(path_or_module).expanduser().resolve()
    if candidate.exists() and candidate.is_dir():
        entries.append(
            {
                "name": name,
                "path": str(candidate),
                "module": f"{name}.cli:plugin",
            }
        )
    else:
        entries.append({"name": name, "module": path_or_module})

    _write_user_plugins(entries)
    typer.echo(f"registered plugin {name!r} → {path_or_module}")


@plugin_app.command("remove")
def remove_plugin(
    name: str = typer.Argument(..., help="Plugin name to deregister."),
) -> None:
    """Remove a plugin from the user manifest."""
    entries = _read_user_plugins()
    new = [e for e in entries if e.get("name") != name]
    if len(new) == len(entries):
        typer.echo(f"no user plugin named {name!r}")
        raise typer.Exit(1)
    _write_user_plugins(new)
    typer.echo(f"removed plugin {name!r}")


__all__ = [
    "ENTRY_POINT_GROUP",
    "load_plugins",
    "plugin_app",
]
