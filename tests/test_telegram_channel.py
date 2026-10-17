"""Telegram long-poll channel — recorded fixture / MockTransport tests."""

from __future__ import annotations

import asyncio
import time
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
from tests.helpers import py_print


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
            exec=py_print("tg-ok"),
            timeout=30,
        )
    )
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "test-token")
    settings = Settings()
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
    settings = Settings()
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
    settings = Settings()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
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
        assert any("getMe" in u for u in seen)
        assert any("deleteWebhook" in u for u in seen)
        assert any("getUpdates" in u for u in seen)
        assert any("stored-bot-token" in u for u in seen)
        assert channel.receiving_ok is True
        assert channel.last_error is None
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
            exec=py_print("polled"),
            timeout=30,
        )
    )
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "poll-token")
    settings = Settings()
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
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
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "backoff-token")
    settings = Settings()
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
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


@pytest.mark.asyncio
async def test_allowlist_blocks_unauthorized_update_and_skips_file_download(
    engine: Engine,
    sample_telegram_payload: dict,
    tmp_path: Path,
) -> None:
    from pboy.channels.telegram import TELEGRAM_ALLOWLIST_SECRET_KEY, parse_allowlist

    assert parse_allowlist(" 111 , -222 \n 333 ") == frozenset({"111", "-222", "333"})
    assert parse_allowlist("") == frozenset()

    # Configure allowlist in store that does NOT include sample chat (-1001) or user (991)
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "test-token")
    await engine.store.put_secret(TELEGRAM_ALLOWLIST_SECRET_KEY, "999999999,-100555555")
    settings = Settings(data_dir=tmp_path)

    get_file_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_file_called
        if "getFile" in request.url.path:
            get_file_called = True
        return httpx.Response(200, json={"ok": True, "result": {}})

    # Attach a photo to verify download is never even attempted for unauthorized sender
    unauthorized_update = {
        "update_id": 2001,
        "message": {
            **sample_telegram_payload["message"],
            "photo": [
                {"file_id": "unauth-photo-id", "file_unique_id": "u1", "width": 10, "height": 10}
            ],
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = TelegramChannel(
            engine.process_event,
            engine.store,
            settings,
            client=client,
        )
        dropped = await channel.handle_update(unauthorized_update)
        assert dropped is None
        assert get_file_called is False
        assert await engine.store.list_events(limit=5) == []

        # Allow by from.id (991) -> passes
        await engine.store.put_secret(TELEGRAM_ALLOWLIST_SECRET_KEY, "991")
        allowed_from = await channel.handle_update(_update_from_sample(sample_telegram_payload, 2002))
        assert allowed_from is not None

        # Clear allowlist (empty string) -> reverts to allow-all
        await engine.store.put_secret(TELEGRAM_ALLOWLIST_SECRET_KEY, "")
        allowed_after_clear = await channel.handle_update(
            _update_from_sample(sample_telegram_payload, 2003)
        )
        assert allowed_after_clear is not None

        # Allow by chat.id (-1001) via store allowlist -> passes
        await engine.store.put_secret(TELEGRAM_ALLOWLIST_SECRET_KEY, "-1001")
        allowed_chat = await channel.handle_update(
            _update_from_sample(sample_telegram_payload, 2004)
        )
        assert allowed_chat is not None


@pytest.mark.asyncio
async def test_telegram_pairing_code_flow(
    engine: Engine,
    sample_telegram_payload: dict,
    tmp_path: Path,
) -> None:
    import json
    import re

    from pboy.channels.telegram import TELEGRAM_ALLOWLIST_SECRET_KEY

    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "pair-token")
    settings = Settings(data_dir=tmp_path)
    sent_messages: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            sent_messages.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(200, json={"ok": True, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = TelegramChannel(
            engine.process_event,
            engine.store,
            settings,
            client=client,
        )
        # 1. Start pairing when no prior chat is known -> waiting mode
        info = await channel.start_pairing()
        assert info["pairing_active"] is True
        assert info["sent_immediately"] is False

        # 2. User messages "/start" in Telegram -> bot replies with 6-digit code, event not dispatched
        start_update = {
            "update_id": 3001,
            "message": {
                **sample_telegram_payload["message"],
                "text": "/start",
            },
        }
        res = await channel.handle_update(start_update)
        assert res is None
        assert len(sent_messages) == 1
        assert sent_messages[0]["chat_id"] == "-1001"
        m = re.search(r"(\d{6})", sent_messages[0]["text"])
        assert m is not None
        code = m.group(1)

        # 3. Wrong code rejected
        with pytest.raises(ValueError, match="유효하지 않거나"):
            await channel.verify_pairing_code("000000")

        # 4. Verify valid code -> stores chat_id and from_id in telegram.allowlist
        ids = await channel.verify_pairing_code(code)
        assert "-1001" in ids
        assert "991" in ids
        assert channel.pairing_active is False
        stored = await engine.store.get_secret(TELEGRAM_ALLOWLIST_SECRET_KEY)
        assert stored == "-1001,991"

        # 5. Subsequent start_pairing when last_seen_peer is known sends code immediately
        info2 = await channel.start_pairing()
        assert info2["sent_immediately"] is True
        assert len(sent_messages) == 2


@pytest.mark.asyncio
async def test_start_rejects_unauthorized_token(engine: Engine) -> None:
    from pboy.channels.telegram import TelegramAuthError

    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "bad-token")
    settings = Settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = TelegramChannel(
        engine.process_event, engine.store, settings, client=client
    )
    with pytest.raises(TelegramAuthError, match="거부"):
        await channel.start()
    assert channel.started is False
    assert channel.receiving_ok is False
    assert channel.last_error is not None
    assert "401" in channel.last_error


@pytest.mark.asyncio
async def test_poll_stops_on_unauthorized(engine: Engine) -> None:
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "later-bad")
    settings = Settings()
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            state["n"] += 1
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=client,
        poll_timeout=1,
        initial_backoff=0.01,
        max_backoff=0.05,
    )
    await channel.start()
    try:
        for _ in range(50):
            if channel.last_error is not None:
                break
            await asyncio.sleep(0.02)
        assert channel.started is False
        assert channel.receiving_ok is False
        assert channel.last_error is not None
        assert "401" in channel.last_error
        polls_after_error = state["n"]
        await asyncio.sleep(0.05)
        assert state["n"] == polls_after_error  # no retry after auth failure
    finally:
        await channel.stop()


@pytest.mark.asyncio
async def test_registry_restarts_telegram_on_bot_token_change(
    engine: Engine, tmp_path: Path
) -> None:
    from pboy.channels.config import save_enabled_to_store
    from pboy.channels.registry import ChannelRegistry

    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "token-v1")
    await save_enabled_to_store(engine.store, ["telegram"])
    settings = Settings(data_dir=tmp_path)
    seen_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # URL path is /bot{token}/method
        parts = request.url.path.split("/")
        if len(parts) >= 3 and parts[1].startswith("bot"):
            seen_tokens.append(parts[1][3:])
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404)

    def factory(emit: Any, store: Any, cfg: Settings) -> TelegramChannel:
        return TelegramChannel(
            emit,
            store,
            cfg,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            poll_timeout=1,
        )

    reg = ChannelRegistry(
        emit=engine.process_event,
        store=engine.store,
        settings=settings,
        factories={"telegram": factory},
    )
    await reg.start_enabled()
    first = reg.get("telegram")
    assert first is not None
    assert first.started is True
    await asyncio.sleep(0.05)

    await reg.set_channel_secret("telegram", "token-v2", secret_name="bot_token")
    second = reg.get("telegram")
    assert second is not None
    assert second is not first
    assert second.started is True
    assert "token-v1" in seen_tokens
    assert "token-v2" in seen_tokens
    # Allowlist must not be cleared by a token change.
    assert await engine.store.get_secret("telegram.allowlist") is None

    await reg.stop_all()


@pytest.mark.asyncio
async def test_emit_queue_backpressure_bounds_pending_and_preserves_offset(
    engine: Engine,
    sample_telegram_payload: dict,
) -> None:
    """When pending emit tasks hit the cap, wait before advancing offset."""
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "bp-token")
    settings = Settings()
    release = asyncio.Event()
    emit_count = 0

    async def slow_emit(
        payload: dict[str, Any], source: str, *, dispatch: bool = True
    ) -> None:
        nonlocal emit_count
        emit_count += 1
        await release.wait()
        return None

    channel = TelegramChannel(
        slow_emit,
        engine.store,
        settings,
        client=httpx.AsyncClient(),
        max_inflight_emits=1,
        max_pending_emits=2,
    )

    # Fill the pending queue (2 tasks); third must block before scheduling / offset advance.
    await channel.handle_update(
        _update_from_sample(sample_telegram_payload, 10), background=True
    )
    await channel.handle_update(
        _update_from_sample(sample_telegram_payload, 11), background=True
    )
    assert channel._offset == 12
    assert len(channel._emit_tasks) == 2

    blocked = asyncio.create_task(
        channel.handle_update(
            _update_from_sample(sample_telegram_payload, 12), background=True
        )
    )
    # Give the waiter a turn; offset must stay until capacity frees.
    await asyncio.sleep(0.05)
    assert not blocked.done()
    assert channel._offset == 12
    assert len(channel._emit_tasks) == 2

    release.set()
    await asyncio.wait_for(blocked, timeout=2.0)
    assert channel._offset == 13
    assert emit_count >= 2
    assert len(channel._emit_tasks) <= 2

    for _ in range(50):
        if not channel._emit_tasks:
            break
        await asyncio.sleep(0.02)
    assert not channel._emit_tasks
    await channel._client.aclose()


@pytest.mark.asyncio
async def test_handle_update_allowlist_store_error_leaves_offset_and_poll_backs_off(
    engine: Engine,
    sample_telegram_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Store errors during allowlist resolve must not hot-loop getUpdates."""
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "err-token")
    settings = Settings()
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            state["n"] += 1
            if state["n"] <= 3:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": [_update_from_sample(sample_telegram_payload, update_id=77)],
                    },
                )
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404)

    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
        poll_timeout=1,
        initial_backoff=0.05,
        max_backoff=0.05,
    )

    async def boom_allowlist() -> frozenset[str]:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(channel, "_resolve_allowlist", boom_allowlist)

    t0 = time.monotonic()
    await channel.start()
    try:
        await asyncio.sleep(0.22)
        # Same update retried with backoff between polls (not a tight spin).
        assert state["n"] >= 2
        assert channel._offset == 0
        assert time.monotonic() - t0 >= 0.1
    finally:
        await channel.stop()
