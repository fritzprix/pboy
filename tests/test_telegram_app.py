"""Telegram MTProto App API — credentials, export formats, list_chats."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from pboy.channels.telegram import TELEGRAM_SECRET_KEY
from pboy.channels.telegram_app import (
    TELEGRAM_API_HASH_SECRET_KEY,
    TELEGRAM_API_ID_SECRET_KEY,
    TELEGRAM_APP_SESSION_SECRET_KEY,
    TelegramAppService,
    format_messages_export,
    resolve_telegram_app_credentials,
)
from pboy.store.sqlite import SQLiteStore


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> SQLiteStore:
    s = SQLiteStore(db_path=tmp_path / "tg-app.db", secret_key="tg-app-key")
    await s.init_db()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_resolve_credentials_requires_api_id_and_hash(store: SQLiteStore) -> None:
    with pytest.raises(RuntimeError, match="api_id"):
        await resolve_telegram_app_credentials(store, require_auth=False)

    await store.put_secret(TELEGRAM_API_ID_SECRET_KEY, "12345")
    with pytest.raises(RuntimeError, match="api_hash"):
        await resolve_telegram_app_credentials(store, require_auth=False)

    await store.put_secret(TELEGRAM_API_HASH_SECRET_KEY, "hash-value")
    # Bot token alone must not satisfy App API auth
    await store.put_secret(TELEGRAM_SECRET_KEY, "bot-token-not-enough")
    with pytest.raises(RuntimeError, match=r"not connected|app_session|계정 연결"):
        await resolve_telegram_app_credentials(store, require_auth=True)

    creds = await resolve_telegram_app_credentials(store, require_auth=False)
    assert creds.api_id == 12345
    assert creds.api_hash == "hash-value"
    assert creds.app_session is None

    await store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, "session-string")
    creds_auth = await resolve_telegram_app_credentials(store, require_auth=True)
    assert creds_auth.app_session == "session-string"


def test_format_messages_export_jsonl_md_csv() -> None:
    messages = [
        {
            "id": 1,
            "chat_id": -1001,
            "date": "2024-01-01T12:00:00Z",
            "text": "hello",
            "sender": {
                "id": 42,
                "username": "alice",
                "display_name": "Alice",
            },
            "reply_to_msg_id": None,
            "media": None,
        },
        {
            "id": 2,
            "chat_id": -1001,
            "date": "2024-01-01T12:01:00Z",
            "text": "world",
            "sender": {"id": 7, "username": None, "display_name": None},
            "reply_to_msg_id": 1,
            "media": {
                "type": "MessageMediaPhoto",
                "file_name": "pic.jpg",
                "size": 99,
                "local_path": "/tmp/pic.jpg",
            },
        },
    ]

    jsonl = format_messages_export(messages, fmt="jsonl", chat_label="chat")
    assert '"hello"' in jsonl
    assert '"world"' in jsonl
    assert jsonl.endswith("\n")
    assert jsonl.count("\n") == 2

    md = format_messages_export(messages, fmt="md", chat_label="My Chat")
    assert "# Telegram Chat Export — My Chat" in md
    assert "Alice (@alice)" in md
    assert "(reply to #1)" in md
    assert "Attachment:" in md
    assert "pic.jpg" in md

    csv_text = format_messages_export(messages, fmt="csv")
    lines = csv_text.strip().split("\n")
    assert lines[0].startswith("id,chat_id,date,")
    assert "hello" in lines[1]
    assert "pic.jpg" in lines[2]
    assert "/tmp/pic.jpg" in lines[2]


@pytest.mark.asyncio
async def test_list_chats_with_mock_client_factory(store: SQLiteStore) -> None:
    await store.put_secret(TELEGRAM_API_ID_SECRET_KEY, "99")
    await store.put_secret(TELEGRAM_API_HASH_SECRET_KEY, "abc")
    await store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, "sess")

    dialog = MagicMock()
    dialog.id = -100555
    dialog.title = "Ops Channel"
    dialog.name = "Ops Channel"
    dialog.is_channel = True
    dialog.is_group = False
    dialog.is_user = False
    dialog.unread_count = 3
    dialog.date = datetime(2024, 6, 1, 10, 0, tzinfo=UTC)
    dialog.entity = MagicMock(username="ops_chan")

    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[dialog])
    client.disconnect = AsyncMock()

    async def factory(
        creds: Any, session_override: str | None, authenticate: bool
    ) -> Any:
        assert creds.api_id == 99
        assert creds.api_hash == "abc"
        assert creds.app_session == "sess"
        assert session_override is None
        assert authenticate is True
        return client

    service = TelegramAppService(store=store, client_factory=factory)
    rows = await service.list_chats(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == -100555
    assert rows[0]["title"] == "Ops Channel"
    assert rows[0]["username"] == "ops_chan"
    assert rows[0]["type"] == "channel"
    assert rows[0]["unread_count"] == 3
    client.get_dialogs.assert_awaited_once_with(limit=10)
    client.disconnect.assert_awaited_once()

    filtered = await service.list_chats(limit=10, search="ops")
    assert len(filtered) == 1
    empty = await service.list_chats(limit=10, search="zzz-no-match")
    assert empty == []


def test_safe_chat_segment_rejects_traversal() -> None:
    from pboy.channels.telegram_app import _safe_chat_segment

    assert _safe_chat_segment(-1001) == "-1001"
    assert _safe_chat_segment("@alice") == "alice"
    with pytest.raises(ValueError, match="Invalid chat path"):
        _safe_chat_segment("..")
    with pytest.raises(ValueError, match="Invalid chat path"):
        _safe_chat_segment("../escape")
    with pytest.raises(ValueError, match="Invalid chat path"):
        _safe_chat_segment("a\\b")


def test_build_get_messages_kwargs_reverse_until() -> None:
    from pboy.channels.telegram_app import _build_get_messages_kwargs

    since = datetime(2024, 1, 1, tzinfo=UTC)
    until = datetime(2024, 6, 1, tzinfo=UTC)

    kwargs, client_since, client_until = _build_get_messages_kwargs(
        limit=10,
        offset_id=0,
        min_id=0,
        max_id=0,
        since_dt=since,
        until_dt=until,
        search=None,
        from_user=None,
        reverse=False,
    )
    assert kwargs["offset_date"] == until
    assert client_since == since
    assert client_until is None

    kwargs_r, client_since_r, client_until_r = _build_get_messages_kwargs(
        limit=10,
        offset_id=0,
        min_id=0,
        max_id=0,
        since_dt=since,
        until_dt=until,
        search=None,
        from_user=None,
        reverse=True,
    )
    assert kwargs_r["offset_date"] == since
    assert client_since_r is None
    assert client_until_r == until


@pytest.mark.asyncio
async def test_list_chats_maps_telethon_rpc_error(store: SQLiteStore) -> None:
    await store.put_secret(TELEGRAM_API_ID_SECRET_KEY, "99")
    await store.put_secret(TELEGRAM_API_HASH_SECRET_KEY, "abc")
    await store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, "sess")

    from telethon.errors import FloodWaitError

    client = AsyncMock()
    client.get_dialogs = AsyncMock(side_effect=FloodWaitError(request=None, capture=5))
    client.disconnect = AsyncMock()

    async def factory(*_a: Any, **_k: Any) -> Any:
        return client

    service = TelegramAppService(store=store, client_factory=factory)
    with pytest.raises(RuntimeError, match="Telegram API error"):
        await service.list_chats()


@pytest.mark.asyncio
async def test_disconnect_clears_app_session(store: SQLiteStore) -> None:
    await store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, "sess")
    service = TelegramAppService(store=store)
    await service.disconnect_account()
    assert await store.get_secret(TELEGRAM_APP_SESSION_SECRET_KEY) == ""


@pytest.mark.asyncio
async def test_changing_api_id_clears_session(store: SQLiteStore) -> None:
    from pboy.channels.registry import ChannelRegistry
    from pboy.core.config import Settings

    await store.put_secret(TELEGRAM_API_ID_SECRET_KEY, "111")
    await store.put_secret(TELEGRAM_API_HASH_SECRET_KEY, "hash")
    await store.put_secret(TELEGRAM_APP_SESSION_SECRET_KEY, "sess")

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    reg = ChannelRegistry(emit=_noop, store=store, settings=Settings())
    await reg.set_channel_secret("telegram", "222", secret_name="api_id")
    assert await store.get_secret(TELEGRAM_APP_SESSION_SECRET_KEY) == ""
