"""``dvr mcp`` sub-commands — MCP server for LLM agents."""

from __future__ import annotations

import typer

app = typer.Typer(name="mcp", help="MCP server: expose dvr to LLM agents.")


@app.command("serve")
def serve(ctx: typer.Context) -> None:
    """Run the MCP server on stdio (the default MCP transport)."""
    cfg = ctx.obj or {}
    try:
        from ...mcp import run_stdio
    except ImportError as exc:
        typer.echo(
            'MCP support requires the optional extra: pip install "dvr[mcp]"',
            err=True,
        )
        raise typer.Exit(1) from exc

    run_stdio(
        auto_launch=cfg.get("auto_launch", True),
        timeout=cfg.get("timeout", 30.0),
    )
