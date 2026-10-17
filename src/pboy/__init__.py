"""Pageboy (`pboy` / `pb`) — local-first Zero-Ops event orchestrator.

Telegram Bot API long-poll and Slack Socket Mode wake-ups on your machine,
topic-matched hooks (``exec``), CloudEvents-shaped events, loopback-only
daemon + ``/ui``.

Quick start::

    uvx pboy ui

Package layout:

- ``cli/`` — Typer CLI (``hook``, ``event``, ``channel``, ``daemon``, ``ui``)
- ``core/`` — normalizer, router, engine
- ``channels/`` — Telegram / Slack wake-up adapters
- ``store/`` — SQLite + encrypted secrets
- ``harness/`` — subprocess executor
- ``ui/`` — embedded local dashboard
- ``models/`` — CloudEvents-compatible domain models
"""

__version__ = "0.3.1"
__author__ = "fritzprix"
