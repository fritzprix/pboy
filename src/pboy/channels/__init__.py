"""Inbound channel adapters (Telegram, Slack, …)."""

from __future__ import annotations

from pboy.channels.base import ChannelAdapter, EventEmitter
from pboy.channels.registry import ChannelRegistry
from pboy.channels.telegram import TelegramChannel

__all__ = ["ChannelAdapter", "ChannelRegistry", "EventEmitter", "TelegramChannel"]
