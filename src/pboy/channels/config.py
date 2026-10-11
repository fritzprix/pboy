"""Persisted channel enablement and per-channel secret key names."""

from __future__ import annotations

import os

from pboy.store.base import StoreBase

CHANNELS_ENABLED_SECRET = "channels.enabled"

# channel name → required encrypted store secret keys
CHANNEL_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "telegram": ("telegram.bot_token",),
    "slack": ("slack.bot_token", "slack.app_token"),
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


def secret_keys_for_channel(name: str) -> tuple[str, ...]:
    return CHANNEL_SECRET_KEYS.get(name.strip().lower(), ())


def resolve_secret_key(channel: str, secret_name: str | None = None) -> str:
    """Resolve store key for ``pboy channel set-secret``.

    Single-secret channels (telegram) omit ``secret_name``.
    Multi-secret channels (slack) require ``bot_token`` / ``app_token`` (or full key).
    """
    key = channel.strip().lower()
    keys = CHANNEL_SECRET_KEYS.get(key)
    if not keys:
        raise ValueError(
            f"Channel '{key}' has no secret mapping. Supported: {', '.join(sorted(CHANNEL_SECRET_KEYS))}"
        )
    if secret_name and secret_name.strip():
        sn = secret_name.strip().lower()
        full = sn if "." in sn else f"{key}.{sn}"
        if full not in keys:
            short = ", ".join(k.split(".", 1)[-1] for k in keys)
            raise ValueError(f"Unknown secret '{secret_name}' for '{key}'. Use one of: {short}")
        return full
    if len(keys) == 1:
        return keys[0]
    short = ", ".join(k.split(".", 1)[-1] for k in keys)
    raise ValueError(f"Channel '{key}' requires --secret-name ({short})")


def secret_key_for_channel(name: str) -> str | None:
    """Backward-compatible: primary secret key, or None if multi-secret without default."""
    keys = secret_keys_for_channel(name)
    if not keys:
        return None
    if len(keys) == 1:
        return keys[0]
    return None


__all__ = [
    "CHANNELS_ENABLED_SECRET",
    "CHANNEL_SECRET_KEYS",
    "env_channels_enabled_override",
    "format_enabled_names",
    "load_enabled_from_store",
    "parse_enabled_names",
    "resolve_secret_key",
    "save_enabled_to_store",
    "secret_key_for_channel",
    "secret_keys_for_channel",
]
