"""Detect removed channel secret env vars so upgrades are not silent."""

from __future__ import annotations

import os

from loguru import logger

# Formerly Settings / override paths; secrets and enablement are store-only now.
REMOVED_CHANNEL_ENV_VARS: tuple[str, ...] = (
    "PB_TELEGRAM_BOT_TOKEN",
    "PB_TELEGRAM_ALLOWLIST",
    "PB_SLACK_BOT_TOKEN",
    "PB_SLACK_APP_TOKEN",
    "PB_CHANNELS_ENABLED",
)


def find_removed_channel_env_vars(
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Return names of removed channel env vars that are still set."""
    env = environ if environ is not None else os.environ
    return [name for name in REMOVED_CHANNEL_ENV_VARS if name in env]


def warn_removed_channel_env_vars(environ: dict[str, str] | None = None) -> list[str]:
    """Log loud errors when obsolete channel env vars are present; return their names.

    ``PB_TELEGRAM_ALLOWLIST`` is the highest risk: an empty store allowlist means
    *any* chat can trigger hooks after upgrade if the operator relied on env only.
    """
    found = find_removed_channel_env_vars(environ)
    for name in found:
        if name == "PB_TELEGRAM_ALLOWLIST":
            logger.error(
                "SECURITY: {} is set but IGNORED. Channel secrets are store-only. "
                "If you never saved telegram.allowlist in /ui/settings, inbound "
                "Telegram is OPEN (any chat can trigger hooks). "
                "Migrate: pboy channel set-secret telegram --secret-name allowlist "
                "--value '<ids>' then unset {}.",
                name,
                name,
            )
        elif name == "PB_CHANNELS_ENABLED":
            logger.error(
                "{} is set but IGNORED. Enable channels via /ui/settings or "
                "`pboy channel enable` (store). Unset {}.",
                name,
                name,
            )
        else:
            logger.error(
                "{} is set but IGNORED. Configure the matching secret in "
                "/ui/settings (encrypted store), then unset {}.",
                name,
                name,
            )
    return found


__all__ = [
    "REMOVED_CHANNEL_ENV_VARS",
    "find_removed_channel_env_vars",
    "warn_removed_channel_env_vars",
]
