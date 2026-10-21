"""Pageboy (`pboy` / `pb`) — local-first Zero-Ops event orchestrator.

Telegram Bot API long-poll and Slack Socket Mode wake-ups on your machine,
topic-matched hooks (``exec``), CloudEvents-shaped events, loopback-only
daemon + ``/ui``, and an MCP stdio server for agents (``pboy mcp``).

Quick start::

    uvx pboy ui

Package layout:

- ``cli/`` — Typer CLI (``hook``, ``event``, ``channel``, ``daemon``, ``ui``, ``mcp``)
- ``core/`` — normalizer, router, engine
- ``channels/`` — Telegram / Slack wake-up adapters
- ``store/`` — SQLite + encrypted secrets
- ``harness/`` — subprocess executor
- ``ui/`` — embedded local dashboard
- ``mcp/`` — MCP stdio tools (events, reply, hooks, status, Telegram App reads)
- ``models/`` — CloudEvents-compatible domain models
"""

__version__ = "0.5.0"
__author__ = "fritzprix"
