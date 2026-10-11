"""Persisted channel enablement and per-channel secret key names."""

from __future__ import annotations

import os

from pboy.store.base import StoreBase

CHANNELS_ENABLED_SECRET = "channels.enabled"

# channel name → encrypted store secret key
CHANNEL_SECRET_KEYS: dict[str, str] = {
    "telegram": "telegram.bot_token",
}


def parse_enabled_names(raw: str | None) -> list[str]:
    """Parse a comma-separated enable list into unique lowercase names."""
    if not raw:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        name = part.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def format_enabled_names(names: list[str]) -> str:
    return ",".join(parse_enabled_names(",".join(names)))


def env_channels_enabled_override() -> str | None:
    """Return raw ``PB_CHANNELS_ENABLED`` when the env var is set (including empty)."""
    if "PB_CHANNELS_ENABLED" in os.environ:
        return os.environ["PB_CHANNELS_ENABLED"]
    return None


async def load_enabled_from_store(store: StoreBase) -> list[str]:
    raw = await store.get_secret(CHANNELS_ENABLED_SECRET)
    return parse_enabled_names(raw)


async def save_enabled_to_store(store: StoreBase, names: list[str]) -> None:
    await store.put_secret(CHANNELS_ENABLED_SECRET, format_enabled_names(names))


def secret_key_for_channel(name: str) -> str | None:
    return CHANNEL_SECRET_KEYS.get(name.strip().lower())


__all__ = [
    "CHANNELS_ENABLED_SECRET",
    "CHANNEL_SECRET_KEYS",
    "env_channels_enabled_override",
    "format_enabled_names",
    "load_enabled_from_store",
    "parse_enabled_names",
    "save_enabled_to_store",
    "secret_key_for_channel",
]
