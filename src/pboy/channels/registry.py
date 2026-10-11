"""Load and lifecycle-manage enabled channel adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger

from pboy.channels.base import ChannelAdapter, EventEmitter
from pboy.channels.config import (
    CHANNEL_SECRET_KEYS,
    env_channels_enabled_override,
    load_enabled_from_store,
    parse_enabled_names,
    save_enabled_to_store,
    secret_key_for_channel,
)
from pboy.channels.fake import FakeChannel
from pboy.channels.telegram import build_telegram_channel
from pboy.core.config import Settings, get_settings
from pboy.store.base import StoreBase

AdapterFactory = Callable[[EventEmitter, StoreBase, Settings], ChannelAdapter | None]


def _fake_factory(emit: EventEmitter, store: StoreBase, settings: Settings) -> ChannelAdapter:
    del store, settings
    return FakeChannel(emit)


BUILTIN_FACTORIES: dict[str, AdapterFactory] = {
    "fake": _fake_factory,
    "telegram": build_telegram_channel,
}


class ChannelRegistry:
    """Register adapters and start only those enabled via env or store."""

    def __init__(
        self,
        emit: EventEmitter,
        store: StoreBase,
        *,
        settings: Settings | None = None,
        factories: dict[str, AdapterFactory] | None = None,
    ) -> None:
        self._emit = emit
        self._store = store
        self._settings = settings or get_settings()
        self._factories = dict(BUILTIN_FACTORIES if factories is None else factories)
        self._instances: dict[str, ChannelAdapter] = {}
        self._running: list[str] = []

    @property
    def running(self) -> list[str]:
        return list(self._running)

    def known_names(self) -> list[str]:
        return sorted(self._factories.keys() | self._instances.keys())

    def register_factory(self, name: str, factory: AdapterFactory) -> None:
        """Register or replace a factory under a lowercase channel name."""
        self._factories[name.strip().lower()] = factory

    def register(self, adapter: ChannelAdapter) -> None:
        """Register a pre-built adapter instance (tests / custom wiring)."""
        self._instances[adapter.name.strip().lower()] = adapter

    def get(self, name: str) -> ChannelAdapter | None:
        return self._instances.get(name.strip().lower())

    async def resolve_enabled_names(self) -> list[str]:
        """Env ``PB_CHANNELS_ENABLED`` overrides store when the variable is set."""
        override = env_channels_enabled_override()
        if override is not None:
            return parse_enabled_names(override)
        return await load_enabled_from_store(self._store)

    def _resolve(self, name: str) -> ChannelAdapter | None:
        key = name.strip().lower()
        if key in self._instances:
            return self._instances[key]
        factory = self._factories.get(key)
        if factory is None:
            logger.warning("channel.unknown name={} — skipping", key)
            return None
        adapter = factory(self._emit, self._store, self._settings)
        if adapter is None:
            logger.warning("channel.unavailable name={} — factory returned None", key)
            return None
        self._instances[key] = adapter
        return adapter

    async def start_enabled(self) -> None:
        """Start adapters listed as enabled; leave others untouched."""
        enabled = await self.resolve_enabled_names()
        if not enabled:
            logger.info("channel.registry no channels enabled")
            return

        for name in enabled:
            await self._start_one(name)

    async def _start_one(self, name: str) -> None:
        key = name.strip().lower()
        if key in self._running:
            return
        adapter = self._resolve(key)
        if adapter is None:
            return
        await adapter.start()
        self._running.append(key)
        logger.info("channel.started name={}", key)

    async def stop_all(self) -> None:
        """Stop every adapter that was started by this registry."""
        for name in reversed(self._running):
            adapter = self._instances.get(name)
            if adapter is None:
                continue
            try:
                await adapter.stop()
                logger.info("channel.stopped name={}", name)
            except Exception:
                logger.exception("channel.stop_failed name={}", name)
        self._running.clear()

    async def enable(self, name: str) -> None:
        """Persist enablement and start the adapter if not already running."""
        key = name.strip().lower()
        if key not in self._factories and key not in self._instances:
            raise ValueError(f"Unknown channel '{key}'. Known: {', '.join(self.known_names())}")
        enabled = await self.resolve_enabled_names()
        if key not in enabled:
            enabled.append(key)
            if env_channels_enabled_override() is None:
                await save_enabled_to_store(self._store, enabled)
            else:
                logger.warning(
                    "channel.enable name={} — PB_CHANNELS_ENABLED is set; "
                    "store not updated (restart with env or unset override)",
                    key,
                )
        await self._start_one(key)

    async def disable(self, name: str) -> None:
        """Persist disablement and stop the adapter if running."""
        key = name.strip().lower()
        enabled = [n for n in await self.resolve_enabled_names() if n != key]
        if env_channels_enabled_override() is None:
            await save_enabled_to_store(self._store, enabled)
        else:
            logger.warning(
                "channel.disable name={} — PB_CHANNELS_ENABLED is set; store not updated",
                key,
            )
        if key in self._running:
            adapter = self._instances.get(key)
            if adapter is not None:
                await adapter.stop()
            self._running = [n for n in self._running if n != key]
            logger.info("channel.stopped name={}", key)

    async def set_channel_secret(self, name: str, value: str) -> str:
        """Store the channel token/secret; returns the secret key used."""
        key = name.strip().lower()
        secret_key = secret_key_for_channel(key)
        if secret_key is None:
            raise ValueError(
                f"Channel '{key}' has no secret mapping. "
                f"Supported: {', '.join(sorted(CHANNEL_SECRET_KEYS))}"
            )
        if not value.strip():
            raise ValueError("Secret value must not be empty")
        await self._store.put_secret(secret_key, value.strip())
        return secret_key

    async def list_status(self) -> list[dict[str, Any]]:
        enabled = set(await self.resolve_enabled_names())
        running = set(self._running)
        rows: list[dict[str, Any]] = []
        for name in self.known_names():
            secret_key = secret_key_for_channel(name)
            has_secret = False
            if secret_key is not None:
                has_secret = (await self._store.get_secret(secret_key)) is not None
            rows.append(
                {
                    "name": name,
                    "enabled": name in enabled,
                    "running": name in running,
                    "secret_key": secret_key,
                    "has_secret": has_secret,
                }
            )
        return rows

    async def status(self) -> dict[str, Any]:
        """Compact status for /healthz."""
        return {
            "enabled": await self.resolve_enabled_names(),
            "running": self.running,
            "known": self.known_names(),
            "env_override": env_channels_enabled_override() is not None,
        }


__all__ = ["BUILTIN_FACTORIES", "AdapterFactory", "ChannelRegistry"]
