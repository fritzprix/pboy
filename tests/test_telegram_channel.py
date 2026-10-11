"""Telegram long-poll channel — recorded fixture / MockTransport tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from pboy.channels.telegram import TELEGRAM_SECRET_KEY, TelegramChannel
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.models.hook import HookCreate
from pboy.store.sqlite import SQLiteStore


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> Engine:
    store = SQLiteStore(db_path=tmp_path / "tg.db", secret_key="tg-test-key")
    eng = Engine(store=store)
    await eng.start()
    yield eng
    await eng.stop()


def _update_from_sample(sample: dict[str, Any], update_id: int = 1001) -> dict[str, Any]:
    return {"update_id": update_id, "message": sample["message"]}


@pytest.mark.asyncio
async def test_handle_update_dispatches_hook(
    engine: Engine,
    sample_telegram_payload: dict,
) -> None:
    await engine.store.create_hook(
        HookCreate(
            name="tg-echo",
            topic="telegram:message",
            exec="echo tg-ok",
            timeout=30,
        )
    )
    settings = Settings(telegram_bot_token="test-token")
    async with httpx.AsyncClient() as client:
        channel = TelegramChannel(
            engine.process_event,
            engine.store,
            settings,
            client=client,
        )
        result = await channel.handle_update(_update_from_sample(sample_telegram_payload))
    assert result is not None
    event, matched, executions = result
    assert event.source == "telegram"
    assert event.type == "com.telegram.message.received"
    assert [h.name for h in matched] == ["tg-echo"]
    assert executions[0].status == "completed"
    assert "tg-ok" in (executions[0].stdout or "")


@pytest.mark.asyncio
async def test_start_requires_token(engine: Engine) -> None:
    settings = Settings(telegram_bot_token="")
    async with httpx.AsyncClient() as client:
        channel = TelegramChannel(
            engine.process_event,
            engine.store,
            settings,
            client=client,
        )
        with pytest.raises(RuntimeError, match="token missing"):
            await channel.start()


@pytest.mark.asyncio
async def test_token_from_store_secret(engine: Engine) -> None:
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "stored-bot-token")
    settings = Settings(telegram_bot_token="")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404, json={"ok": False})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.telegram.org")
    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=client,
        api_base="https://api.telegram.org",
        poll_timeout=1,
    )
    await channel.start()
    try:
        assert channel.started is True
        await asyncio.sleep(0.05)
        assert any("deleteWebhook" in u for u in seen)
        assert any("getUpdates" in u for u in seen)
        assert any("stored-bot-token" in u for u in seen)
    finally:
        await channel.stop()


@pytest.mark.asyncio
async def test_poll_loop_emits_recorded_update(
    engine: Engine,
    sample_telegram_payload: dict,
) -> None:
    await engine.store.create_hook(
        HookCreate(
            name="tg-poll",
            topic="telegram:message",
            exec="echo polled",
            timeout=30,
        )
    )
    settings = Settings(telegram_bot_token="poll-token")
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": [_update_from_sample(sample_telegram_payload, update_id=55)],
                    },
                )
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.telegram.org",
    )
    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=client,
        api_base="https://api.telegram.org",
        poll_timeout=1,
    )
    await channel.start()
    try:
        for _ in range(50):
            events = await engine.store.list_events(limit=5)
            if events:
                break
            await asyncio.sleep(0.05)
        events = await engine.store.list_events(limit=5)
        assert len(events) >= 1
        assert events[0].source == "telegram"
        assert events[0].data.get("summary") == "Restart worker-2"
    finally:
        await channel.stop()


@pytest.mark.asyncio
async def test_poll_backoff_on_error_then_recovers(
    engine: Engine,
    sample_telegram_payload: dict,
) -> None:
    settings = Settings(telegram_bot_token="backoff-token")
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(500, text="boom")
            if state["n"] == 2:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": [_update_from_sample(sample_telegram_payload, update_id=7)],
                    },
                )
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=client,
        api_base="https://api.telegram.org",
        poll_timeout=1,
        initial_backoff=0.01,
        max_backoff=0.05,
    )
    await channel.start()
    try:
        for _ in range(100):
            events = await engine.store.list_events(limit=5)
            if events:
                break
            await asyncio.sleep(0.02)
        assert await engine.store.list_events(limit=5)
    finally:
        await channel.stop()
