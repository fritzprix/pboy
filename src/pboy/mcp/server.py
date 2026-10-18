"""Pageboy MCP server (stdio) — curated tools for chat → investigate → reply."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from pboy.mcp.backend import McpBackend, McpBackendError

INSTRUCTIONS = """\
Pageboy MCP — local event orchestrator tools.

Workflow: list_events / get_event → investigate → reply_event.
Hooks: list_hooks / register_hook / delete_hook.
status checks daemon + channels.

telegram_list_chats / telegram_list_messages are Telegram App API reads
(user account). Do NOT use them to send bot replies — use reply_event instead.

Not exposed: event inject, secret writes, daemon start/stop.
"""

# Explicit allowlist so tests/docs can assert the surface stays curated.
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "list_events",
        "get_event",
        "reply_event",
        "list_hooks",
        "register_hook",
        "delete_hook",
        "status",
        "telegram_list_chats",
        "telegram_list_messages",
    }
)


def create_mcp_server(
    *,
    daemon_url: str | None = None,
    backend: McpBackend | None = None,
) -> MCPServer[Any]:
    """Build an MCP server bound to a ``McpBackend`` (daemon or local SQLite)."""
    be = backend or McpBackend(daemon_url=daemon_url)
    mcp = MCPServer("pboy", instructions=INSTRUCTIONS)

    @mcp.tool()
    def status() -> dict[str, Any]:
        """Daemon health and channel status (or local SQLite fallback)."""
        return _call(be.status)

    @mcp.tool()
    def list_events(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """List recent CloudEvents (id, source, type, time)."""
        return _call(lambda: be.list_events(limit=limit, offset=offset))

    @mcp.tool()
    def get_event(event_id: str) -> dict[str, Any]:
        """Get event detail including hook execution output when available."""
        return _call(lambda: be.get_event(event_id))

    @mcp.tool()
    def reply_event(
        event_id: str,
        summary: str = "Completed",
        sync: str | None = None,
    ) -> dict[str, Any]:
        """Reply on the originating chat (Telegram bot or Slack thread).

        Default sync target is derived from the event source. This is bot-token
        outbound — not Telegram App API (chats/messages).
        """
        return _call(
            lambda: be.reply_event(event_id, summary=summary, sync=sync)
        )

    @mcp.tool()
    def list_hooks() -> dict[str, Any]:
        """List registered hooks."""
        return _call(be.list_hooks)

    @mcp.tool()
    def register_hook(
        name: str,
        topic: str,
        command: str,
        filter_expr: str | None = None,
        timeout: int = 180,
        concurrency: int = 1,
    ) -> dict[str, Any]:
        """Register a hook. ``command`` is the shell exec string (stdin = CloudEvent JSON).

        Topics e.g. telegram:message, slack:message. Filters: path ==|!= value only.
        """
        return _call(
            lambda: be.register_hook(
                name=name,
                topic=topic,
                command=command,
                filter=filter_expr,
                timeout=timeout,
                concurrency=concurrency,
            )
        )

    @mcp.tool()
    def delete_hook(name: str) -> dict[str, Any]:
        """Delete a registered hook by name."""
        return _call(lambda: be.delete_hook(name))

    @mcp.tool()
    def telegram_list_chats(
        limit: int = 50,
        search: str | None = None,
    ) -> dict[str, Any]:
        """List Telegram dialogs via App API (user account). Not for bot replies."""
        return _call(lambda: be.telegram_list_chats(limit=limit, search=search))

    @mcp.tool()
    def telegram_list_messages(
        chat: str,
        limit: int = 50,
        offset_id: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Fetch messages from a chat via App API (read-only). Not for bot replies."""
        return _call(
            lambda: be.telegram_list_messages(
                chat, limit=limit, offset_id=offset_id, search=search
            )
        )

    return mcp


def run_mcp_stdio(*, daemon_url: str | None = None) -> None:
    """Serve over stdio (MCP host launches ``pboy mcp`` as a subprocess)."""
    create_mcp_server(daemon_url=daemon_url).run(transport="stdio")


def _call(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except McpBackendError as exc:
        raise ToolError(str(exc)) from exc
