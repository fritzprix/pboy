"""MCP server for Pageboy — agent tools for chat → investigate → reply.

Exposes a curated tool surface (events, reply, hooks, status, optional Telegram
App API reads). Test/debug inject and secret-write paths are intentionally omitted.
"""

from __future__ import annotations

from pboy.mcp.server import create_mcp_server, run_mcp_stdio

__all__ = ["create_mcp_server", "run_mcp_stdio"]
