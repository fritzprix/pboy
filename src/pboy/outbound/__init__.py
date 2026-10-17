"""Outbound sync clients (reply / comment back to source platforms)."""

from __future__ import annotations

from pboy.outbound.slack import SlackOutbound
from pboy.outbound.telegram import TelegramOutbound

__all__ = ["SlackOutbound", "TelegramOutbound"]
