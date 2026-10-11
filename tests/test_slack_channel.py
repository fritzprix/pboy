"""Slack Socket Mode channel — envelope handling (no live Slack)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from pboy.channels.config import resolve_secret_key
from pboy.channels.slack import SlackChannel
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.models.hook import HookCreate
from pboy.store.sqlite import SQLiteStore


async def _noop_emit(*_a: Any, **_k: Any) -> None:
    return None


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> Engine:
    store = SQLiteStore(db_path=tmp_path / "slack.db", secret_key="slack-test-key")
    eng = Engine(
        store=store,
        settings=Settings(slack_bot_token="xoxb-test", slack_app_token="xapp-test"),
    )
    await eng.start()
    yield eng
    await eng.stop()


def test_resolve_slack_secret_keys() -> None:
    assert resolve_secret_key("slack", "bot_token") == "slack.bot_token"
    assert resolve_secret_key("slack", "app_token") == "slack.app_token"
    with pytest.raises(ValueError, match="secret-name"):
        resolve_secret_key("slack", None)


@pytest.mark.asyncio
async def test_handle_envelope_dispatches_message(
    engine: Engine,
    sample_slack_payload: dict,
) -> None:
    await engine.store.create_hook(
        HookCreate(
            name="slack-echo",
            topic="slack:message",
            exec="echo slack-ok",
            timeout=30,
        )
    )
    channel = SlackChannel(
        engine.process_event,
        engine.store,
        engine.settings,
    )
    envelope = {
        "envelope_id": "env-1",
        "type": "events_api",
        "payload": sample_slack_payload,
    }
    result = await channel.handle_envelope(envelope, dispatch=True)
    assert result is not None
    event, matched, executions = result
    assert event.source == "slack"
    assert event.type == "com.slack.message.received"
    assert [h.name for h in matched] == ["slack-echo"]
    assert executions[0].status == "completed"
    assert "slack-ok" in (executions[0].stdout or "")


@pytest.mark.asyncio
async def test_handle_envelope_skips_bot_messages(engine: Engine) -> None:
    channel = SlackChannel(engine.process_event, engine.store, engine.settings)
    envelope = {
        "envelope_id": "env-2",
        "type": "events_api",
        "payload": {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "text": "ignore",
                "channel": "C1",
                "bot_id": "B1",
                "ts": "1.0",
            }
        },
    }
    assert await channel.handle_envelope(envelope) is None


@pytest.mark.asyncio
async def test_start_requires_both_tokens(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=tmp_path / "s3.db", secret_key="slack-key")
    await store.init_db()
    try:
        channel = SlackChannel(
            _noop_emit,
            store,
            Settings(slack_bot_token="", slack_app_token=""),
        )
        with pytest.raises(RuntimeError, match="tokens missing"):
            await channel.start()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_handle_envelope_acks_socket(engine: Engine) -> None:
    channel = SlackChannel(engine.process_event, engine.store, engine.settings)
    mock_ws = AsyncMock()
    channel._ws = mock_ws
    envelope = {
        "envelope_id": "env-ack-42",
        "type": "events_api",
        "payload": {"event": {"type": "message", "text": "hi", "channel": "C1"}},
    }
    await channel.handle_envelope(envelope, dispatch=False)
    mock_ws.send.assert_awaited_once_with('{"envelope_id": "env-ack-42"}')


@pytest.mark.asyncio
async def test_handle_envelope_disconnect_signals_reconnect(engine: Engine) -> None:
    channel = SlackChannel(engine.process_event, engine.store, engine.settings)
    result = await channel.handle_envelope(
        {"type": "disconnect", "reason": "refresh_requested"}
    )
    assert result is SlackChannel.RECONNECT


@pytest.mark.asyncio
async def test_handle_envelope_background_does_not_await_emit(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=tmp_path / "bg.db", secret_key="slack-bg-key")
    await store.init_db()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_emit(*_a: Any, **_k: Any) -> None:
        started.set()
        await asyncio.sleep(30)
        finished.set()

    try:
        channel = SlackChannel(
            slow_emit,
            store,
            Settings(slack_bot_token="xoxb-t", slack_app_token="xapp-t"),
        )
        mock_ws = AsyncMock()
        channel._ws = mock_ws
        envelope = {
            "envelope_id": "env-bg-1",
            "type": "events_api",
            "payload": {
                "event": {"type": "message", "text": "hi", "channel": "C1"},
            },
        }
        result = await asyncio.wait_for(
            channel.handle_envelope(envelope, background=True),
            timeout=0.5,
        )
        assert result is None
        mock_ws.send.assert_awaited_once_with('{"envelope_id": "env-bg-1"}')
        await asyncio.wait_for(started.wait(), timeout=0.5)
        assert not finished.is_set()
        await channel.stop()
        assert not channel._emit_tasks
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_start_stop_restart_lifecycle(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=tmp_path / "s_restart.db", secret_key="slack-restart-key")
    await store.init_db()
    try:
        channel = SlackChannel(
            _noop_emit,
            store,
            Settings(slack_bot_token="xoxb-t", slack_app_token="xapp-t"),
        )
        await channel.start()
        assert channel.started is True
        await channel.stop()
        assert channel.started is False
        assert channel._client.is_closed is True

        # Re-start must re-open client and succeed
        await channel.start()
        assert channel.started is True
        assert channel._client.is_closed is False
        await channel.stop()
    finally:
        await store.close()


def test_secret_key_for_channel_multi_secret() -> None:
    from pboy.channels.config import secret_key_for_channel

    assert secret_key_for_channel("telegram") == "telegram.bot_token"
    assert secret_key_for_channel("slack") is None
    assert secret_key_for_channel("fake") is None
