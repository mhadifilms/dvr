"""``dvr spec`` sub-commands — spec file tooling."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml

from ... import spec as spec_mod
from .. import output
from ..session import resolve_from_ctx as _resolve

app = typer.Typer(name="spec", help="Spec tooling: adopt live projects into declarative specs.")


@app.command("export")
def export(
    ctx: typer.Context,
    project: Annotated[
        str | None,
        typer.Argument(help="Project to export. Defaults to the current project."),
    ] = None,
    out: Annotated[
        str | None,
        typer.Option("--out", "-o", help="Write the spec to this file (YAML) instead of stdout."),
    ] = None,
) -> None:
    """Build a spec from live project state (the inverse of `dvr apply`).

    Captures the color/format settings subset, the bin tree, and each
    timeline's fps, track counts, and markers — so an existing project
    can be adopted into a spec file and managed with `dvr plan` / `dvr
    apply` from then on.
    """
    r = _resolve(ctx)
    data = spec_mod.from_live(r, project=project)
    if out:
        Path(out).expanduser().write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        output.emit({"exported": data["project"], "path": out}, fmt=ctx.obj["format"])
        return
    output.emit(data, fmt=ctx.obj["format"], headline=f"spec: {data['project']}")
