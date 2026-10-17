"""Telegram inbound attachments + outbound reply-file sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from pboy.channels.telegram import TELEGRAM_SECRET_KEY, TelegramChannel
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.core.normalizer import EventNormalizer
from pboy.core.telegram_media import extract_telegram_attachments
from pboy.models.cloudevent import CloudEvent
from pboy.models.reply_file import ReplyFile
from pboy.outbound.telegram import TelegramOutbound
from pboy.store.sqlite import SQLiteStore


def _photo_message() -> dict[str, Any]:
    return {
        "message_id": 99,
        "caption": "see this",
        "from": {"id": 1, "username": "u"},
        "chat": {"id": -50, "type": "private"},
        "photo": [
            {"file_id": "small", "file_unique_id": "s", "width": 10, "height": 10, "file_size": 100},
            {
                "file_id": "big-file-id",
                "file_unique_id": "b",
                "width": 800,
                "height": 600,
                "file_size": 9000,
            },
        ],
    }


def test_extract_largest_photo() -> None:
    atts = extract_telegram_attachments(_photo_message())
    assert len(atts) == 1
    assert atts[0].file_id == "big-file-id"
    assert atts[0].file_type == "photo"
    assert atts[0].file_size == 9000


def test_extract_document() -> None:
    atts = extract_telegram_attachments(
        {
            "document": {
                "file_id": "doc-1",
                "file_unique_id": "d",
                "file_name": "a.pdf",
                "mime_type": "application/pdf",
                "file_size": 12,
            }
        }
    )
    assert atts[0].file_type == "document"
    assert atts[0].file_name == "a.pdf"


def test_normalize_photo_caption_and_attachments() -> None:
    event = EventNormalizer().normalize({"message": _photo_message()}, "telegram")
    assert event.data["summary"] == "see this"
    atts = event.data["attachments"]
    assert len(atts) == 1
    assert atts[0]["file_id"] == "big-file-id"
    assert atts[0]["file_type"] == "photo"
    assert "local_path" not in atts[0]


def test_normalize_text_only_unchanged(sample_telegram_payload: dict) -> None:
    event = EventNormalizer().normalize(sample_telegram_payload, "telegram")
    assert event.data["summary"] == "Restart worker-2"
    assert "attachments" not in event.data


@pytest.mark.asyncio
async def test_channel_downloads_photo_before_emit(
    tmp_path: Path, engine: Engine
) -> None:
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "tok")
    settings = Settings(
        data_dir=tmp_path / "data",
        telegram_download_files=True,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    blob = b"fake-jpeg-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "file_id": "big-file-id",
                        "file_path": "photos/file_1.jpg",
                        "file_size": len(blob),
                    },
                },
            )
        if "/file/bot" in str(request.url):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=client,
        api_base="https://api.telegram.org",
    )
    result = await channel.handle_update(
        {"update_id": 7, "message": _photo_message()},
        dispatch=False,
    )
    assert result is not None
    event, _matched, _execs = result
    atts = event.data["attachments"]
    assert atts[0]["file_id"] == "big-file-id"
    local = Path(atts[0]["local_path"])
    assert local.is_file()
    assert local.read_bytes() == blob


@pytest.mark.asyncio
async def test_channel_emits_metadata_when_download_fails(
    tmp_path: Path, engine: Engine
) -> None:
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "tok")
    settings = Settings(
        data_dir=tmp_path / "data",
        telegram_download_files=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channel = TelegramChannel(
        engine.process_event,
        engine.store,
        settings,
        client=client,
        api_base="https://api.telegram.org",
    )
    result = await channel.handle_update(
        {"update_id": 8, "message": _photo_message()},
        dispatch=False,
    )
    assert result is not None
    event, _, _ = result
    assert event.data["attachments"][0]["file_id"] == "big-file-id"
    assert "local_path" not in event.data["attachments"][0]


@pytest.mark.asyncio
async def test_outbound_send_photo_by_path(tmp_path: Path) -> None:
    files_root = tmp_path / "files"
    files_root.mkdir(parents=True)
    photo = files_root / "out.jpg"
    photo.write_bytes(b"jpeg")
    store = SQLiteStore(db_path=tmp_path / "o.db", secret_key="k")
    await store.init_db()
    await store.put_secret(TELEGRAM_SECRET_KEY, "tok")
    settings = Settings(data_dir=tmp_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 77}}
        )

    outbound = TelegramOutbound(
        store,
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
    )
    event = CloudEvent(
        source="telegram",
        type="com.telegram.message.received",
        data={"channel_id": "-1001", "entity_key": "42"},
    )
    try:
        result = await outbound.sync_completion(
            event,
            "here you go",
            reply_file=ReplyFile(file_path=str(photo), file_type="photo"),
        )
    finally:
        await outbound.aclose()
        await store.close()
    assert result.ok is True
    assert result.message_id == "77"
    assert seen["path"].endswith("/sendPhoto")
    assert "multipart" in seen["content_type"]


def test_resolve_reply_file_rejects_outside_files_root(tmp_path: Path) -> None:
    from pboy.models.reply_file import resolve_reply_file_path

    root = tmp_path / "files"
    root.mkdir()
    outside = tmp_path / "secret.bin"
    outside.write_bytes(b"x")
    with pytest.raises(PermissionError, match="must be under"):
        resolve_reply_file_path(str(outside), files_root=root)


def test_stage_reply_file_uses_unique_names(tmp_path: Path) -> None:
    from pboy.models.reply_file import stage_reply_file_for_daemon

    root = tmp_path / "files"
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "report.pdf").write_bytes(b"aaa")
    (b_dir / "report.pdf").write_bytes(b"bbb")
    staged_a = stage_reply_file_for_daemon(a_dir / "report.pdf", files_root=root)
    staged_b = stage_reply_file_for_daemon(b_dir / "report.pdf", files_root=root)
    assert staged_a != staged_b
    assert staged_a.is_file() and staged_b.is_file()
    assert staged_a.read_bytes() == b"aaa"
    assert staged_b.read_bytes() == b"bbb"


def test_truncate_telegram_caption() -> None:
    from pboy.models.reply_file import TELEGRAM_CAPTION_MAX, truncate_telegram_caption

    short = "ok"
    assert truncate_telegram_caption(short) == short
    long = "x" * (TELEGRAM_CAPTION_MAX + 50)
    out = truncate_telegram_caption(long)
    assert len(out) == TELEGRAM_CAPTION_MAX
    assert out.endswith("…")


def test_inject_payload_cannot_set_local_paths() -> None:
    """HTTP inject must not honor forged local_path maps (contextvar-only)."""
    event = EventNormalizer().normalize(
        {
            "message": _photo_message(),
            "_pboy_local_files": {"big-file-id": "/etc/passwd"},
        },
        "telegram",
    )
    assert "local_path" not in event.data["attachments"][0]


@pytest.mark.asyncio
async def test_complete_with_reply_file_after_successful_text_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteStore(db_path=tmp_path / "retry.db", secret_key="k")
    files_root = tmp_path / "files"
    files_root.mkdir()
    photo = files_root / "again.jpg"
    photo.write_bytes(b"jpeg")
    settings = Settings(data_dir=tmp_path)
    eng = Engine(store=store, settings=settings)
    await eng.start()
    await eng.store.put_secret(TELEGRAM_SECRET_KEY, "tok")
    event = CloudEvent(
        source="telegram",
        type="com.telegram.message.received",
        data={"channel_id": "-1", "entity_key": "1"},
    )
    await eng.store.create_event(event)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    import pboy.core.engine as engine_mod

    real = engine_mod.TelegramOutbound

    def _factory(*args: Any, **kwargs: Any) -> TelegramOutbound:
        kwargs.setdefault(
            "client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        kwargs.setdefault("api_base", "https://api.telegram.org")
        return real(*args, **kwargs)

    monkeypatch.setattr(engine_mod, "TelegramOutbound", _factory)
    try:
        _first, already1 = await eng.complete_event(
            event.id, summary="text", sync=["telegram"]
        )
        assert already1 is False
        assert any(p.endswith("/sendMessage") for p in seen)
        seen.clear()
        second, already2 = await eng.complete_event(
            event.id,
            summary="with file",
            sync=["telegram"],
            reply_file=ReplyFile(file_path=str(photo), file_type="photo"),
        )
        assert already2 is True
        assert second.summary == "text"  # original summary kept
        assert any(p.endswith("/sendPhoto") for p in seen)
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_outbound_send_document_by_file_id(tmp_path: Path) -> None:
    store = SQLiteStore(db_path=tmp_path / "o2.db", secret_key="k")
    await store.init_db()
    await store.put_secret(TELEGRAM_SECRET_KEY, "tok")
    settings = Settings(data_dir=tmp_path)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 3}}
        )

    outbound = TelegramOutbound(
        store,
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
    )
    event = CloudEvent(
        source="telegram",
        type="com.telegram.message.received",
        data={"channel_id": "99"},
    )
    try:
        result = await outbound.sync_completion(
            event,
            "fwd",
            reply_file=ReplyFile(file_id="AgAC-doc", file_type="document"),
        )
    finally:
        await outbound.aclose()
        await store.close()
    assert result.ok is True
    assert seen["path"].endswith("/sendDocument")
    assert "AgAC-doc" in seen["body"]


@pytest.mark.asyncio
async def test_outbound_ignores_inbound_attachments_without_reply_file(
    tmp_path: Path,
) -> None:
    """Without explicit reply_file, sync stays sendMessage even if event has attachments."""
    store = SQLiteStore(db_path=tmp_path / "c.db", secret_key="k")
    await store.init_db()
    await store.put_secret(TELEGRAM_SECRET_KEY, "tok")
    settings = Settings(data_dir=tmp_path)
    seen: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 1}}
        )

    outbound = TelegramOutbound(
        store,
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
    )
    event = CloudEvent(
        source="telegram",
        type="com.telegram.message.received",
        data={
            "channel_id": "-1",
            "entity_key": "1",
            "attachments": [{"file_id": "inbound", "file_type": "photo"}],
        },
    )
    try:
        result = await outbound.sync_completion(event, "done")
    finally:
        await outbound.aclose()
        await store.close()

    assert result.ok is True
    assert any(p.endswith("/sendMessage") for p in seen)
    assert not any("sendPhoto" in p for p in seen)
