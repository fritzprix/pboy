"""Removed channel env vars must surface loudly at daemon start."""

from __future__ import annotations

from pboy.channels.env_contract import (
    REMOVED_CHANNEL_ENV_VARS,
    find_removed_channel_env_vars,
    warn_removed_channel_env_vars,
)


def test_find_removed_channel_env_vars() -> None:
    assert find_removed_channel_env_vars({}) == []

    found = find_removed_channel_env_vars(
        {
            "PB_TELEGRAM_ALLOWLIST": "1,2",
            "PB_CHANNELS_ENABLED": "telegram",
            "PATH": "/usr/bin",
        }
    )
    assert found == ["PB_TELEGRAM_ALLOWLIST", "PB_CHANNELS_ENABLED"]
    assert set(REMOVED_CHANNEL_ENV_VARS) >= set(found)


def test_warn_removed_channel_env_vars_returns_names() -> None:
    found = warn_removed_channel_env_vars(
        {
            "PB_TELEGRAM_ALLOWLIST": "9",
            "PB_TELEGRAM_BOT_TOKEN": "tok",
        }
    )
    assert found == ["PB_TELEGRAM_BOT_TOKEN", "PB_TELEGRAM_ALLOWLIST"]
