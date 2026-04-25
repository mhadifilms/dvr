"""``dvr schema`` — discoverable catalogs for valid settings, codecs, properties."""

from __future__ import annotations

from typing import Annotated

import typer

from ... import schema as schema_mod
from ...resolve import Resolve
from .. import output

app = typer.Typer(name="schema", help="Catalogs of valid setting keys, codecs, properties.")


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


_LIVE_TOPICS = {"render-formats", "render-codecs", "render-presets"}


@app.command("topics")
def topics(ctx: typer.Context) -> None:
    """List available schema topics."""
    rows = [
        {
            "topic": t,
            "needs_resolve_running": t in _LIVE_TOPICS,
        }
        for t in schema_mod.TOPICS
    ]
    output.emit(rows, fmt=ctx.obj["format"], headline="schema topics")


@app.command("show")
def show(
    ctx: typer.Context,
    topic: Annotated[str, typer.Argument(help=f"One of: {', '.join(schema_mod.TOPICS)}.")],
) -> None:
    """Print the catalog for a topic."""
    if topic in _LIVE_TOPICS:
        r = _resolve(ctx)
        data = schema_mod.get_topic(topic, r)
    else:
        data = schema_mod.get_topic(topic)
    output.emit(data, fmt=ctx.obj["format"], headline=f"schema: {topic}")
