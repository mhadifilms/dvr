"""Model Context Protocol server for `dvr`.

Exposes the public library as MCP tools so LLM agents can drive
DaVinci Resolve through typed schemas instead of shell commands.

Run with::

    pip install "dvr[mcp]"
    dvr mcp serve

The server uses stdio transport by default — clients spawn it as a
subprocess. Each MCP tool is one library call wrapped in error
serialization.
"""

from __future__ import annotations

from .server import build_server, run_stdio

__all__ = ["build_server", "run_stdio"]
