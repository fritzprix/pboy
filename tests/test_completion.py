"""Event completion persistence, idempotency, and Telegram outbound sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from pboy.cli.main import app
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.daemon.app import create_app
from pboy.models.cloudevent import CloudEvent
from pboy.models.completion import COMPLETION_STATE_KEY, EXECUTION_STATE_KEY, parse_sync_targets
from pboy.store.sqlite import SQLiteStore


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> Engine:
    store = SQLiteStore(db_path=tmp_path / "complete.db", secret_key="complete-key")
    eng = Engine(store=store, settings=Settings(telegram_bot_token="tok"))
    await eng.start()
    yield eng
    await eng.stop()


async def _seed_telegram_event(engine: Engine) -> CloudEvent:
    event = CloudEvent(
        source="telegram",
        type="com.telegram.message.received",
        data={
            "entity_key": "42",
            "channel_id": "-1001",
            "summary": "Restart worker-2",
        },
    )
    await engine.store.create_event(event)
    return event


def test_parse_sync_targets() -> None:
    assert parse_sync_targets(None) == []
    assert parse_sync_targets("telegram") == ["telegram"]
    assert parse_sync_targets("telegram:message, telegram") == ["telegram"]


@pytest.mark.asyncio
async def test_complete_event_persists_and_is_idempotent(engine: Engine) -> None:
    event = await _seed_telegram_event(engine)
    first, already1 = await engine.complete_event(event.id, summary="All good")
    assert already1 is False
    assert first.status == "completed"
    assert first.summary == "All good"

    stored = await engine.store.get_state(event.id, COMPLETION_STATE_KEY)
    assert stored is not None
    assert stored["status"] == "completed"
    exec_state = await engine.store.get_state(event.id, EXECUTION_STATE_KEY)
    assert exec_state is not None
    assert exec_state["progress"] == 100

    second, already2 = await engine.complete_event(event.id, summary="Ignored")
    assert already2 is True
    assert second.summary == "All good"

    detail = await engine.event_detail(event.id)
    assert detail is not None
    assert detail["completion"]["summary"] == "All good"


@pytest.mark.asyncio
async def test_complete_missing_event_raises(engine: Engine) -> None:
    with pytest.raises(KeyError, match="not found"):
        await engine.complete_event("evt_missing")


@pytest.mark.asyncio
async def test_telegram_outbound_send_message(engine: Engine) -> None:
    from pboy.outbound.telegram import TelegramOutbound

    event = await _seed_telegram_event(engine)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sendMessage"):
            seen.append(request.read().decode())
            return httpx.Response(
                200,
                json={"ok": True, "result": {"message_id": 99, "chat": {"id": -1001}}},
            )
        return httpx.Response(404)

    outbound = TelegramOutbound(
        engine.store,
        engine.settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
    )
    try:
        result = await outbound.sync_completion(event, "Fixed")
    finally:
        await outbound.aclose()
    assert result.ok is True
    assert result.message_id == "99"
    assert seen and "Fixed" in seen[0]
    assert "reply_to_message_id" in seen[0]


@pytest.mark.asyncio
async def test_telegram_outbound_error_redacts_token(engine: Engine) -> None:
    from pboy.outbound.telegram import TelegramOutbound

    event = await _seed_telegram_event(engine)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, request=request)

    outbound = TelegramOutbound(
        engine.store,
        engine.settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
    )
    try:
        result = await outbound.sync_completion(event, "Boom")
    finally:
        await outbound.aclose()
    assert result.ok is False
    assert "[REDACTED]" in result.detail
    assert "tok" not in result.detail


@pytest.mark.asyncio
async def test_complete_event_runs_telegram_sync(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pboy.models.completion import SyncResult
    from pboy.outbound import telegram as tg_mod

    event = await _seed_telegram_event(engine)
    calls: list[str] = []

    async def _fake_sync(self: Any, evt: CloudEvent, summary: str) -> SyncResult:
        del self, evt
        calls.append(summary)
        return SyncResult(target="telegram", ok=True, detail="sent", message_id="7")

    monkeypatch.setattr(tg_mod.TelegramOutbound, "sync_completion", _fake_sync)

    completion, already = await engine.complete_event(
        event.id, summary="Fixed", sync=["telegram"]
    )
    assert already is False
    assert calls == ["Fixed"]
    assert completion.sync_results[0].ok is True
    assert completion.sync_results[0].message_id == "7"


@pytest.mark.asyncio
async def test_daemon_complete_and_get_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pboy.core.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None)
    settings = Settings(telegram_bot_token="tok")
    app_api = create_app(
        db_path=tmp_path / "d-complete.db",
        secret_key="d-complete-key",
        settings=settings,
    )
    eng: Engine = app_api.state.engine
    transport = ASGITransport(app=app_api)
    async with (
        app_api.router.lifespan_context(app_api),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        event = await _seed_telegram_event(eng)
        res = await client.post(
            f"/v1/events/{event.id}/complete",
            json={"summary": "Done via API", "sync": []},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["already_completed"] is False
        assert body["summary"] == "Done via API"

        again = await client.post(
            f"/v1/events/{event.id}/complete",
            json={"summary": "other", "sync": []},
        )
        assert again.status_code == 200
        assert again.json()["already_completed"] is True

        got = await client.get(f"/v1/events/{event.id}")
        assert got.status_code == 200
        detail = got.json()
        assert detail["completion"]["summary"] == "Done via API"
        assert detail["execution_state"]["status"] == "completed"


@pytest.mark.asyncio
async def test_complete_retries_failed_sync_only(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pboy.models.completion import SyncResult
    from pboy.outbound import telegram as tg_mod

    event = await _seed_telegram_event(engine)
    outcomes = iter(
        [
            SyncResult(target="telegram", ok=False, detail="boom"),
            SyncResult(target="telegram", ok=True, detail="sent", message_id="9"),
        ]
    )
    calls: list[str] = []

    async def _fake_sync(self: Any, evt: CloudEvent, summary: str) -> SyncResult:
        del self, evt
        calls.append(summary)
        return next(outcomes)

    monkeypatch.setattr(tg_mod.TelegramOutbound, "sync_completion", _fake_sync)

    first, already1 = await engine.complete_event(
        event.id, summary="KeepMe", sync=["telegram"]
    )
    assert already1 is False
    assert first.sync_results[0].ok is False

    second, already2 = await engine.complete_event(
        event.id, summary="ShouldNotReplace", sync=["telegram"]
    )
    assert already2 is True
    assert second.summary == "KeepMe"
    assert second.sync_results[0].ok is True
    assert calls == ["KeepMe", "KeepMe"]

    third, already3 = await engine.complete_event(
        event.id, summary="x", sync=["telegram"]
    )
    assert already3 is True
    assert calls == ["KeepMe", "KeepMe"]  # no third outbound
    assert third.sync_results[0].ok is True


@pytest.mark.asyncio
async def test_concurrent_complete_runs_sync_once(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from pboy.models.completion import SyncResult
    from pboy.outbound import telegram as tg_mod

    event = await _seed_telegram_event(engine)
    calls: list[int] = []

    async def _fake_sync(self: Any, evt: CloudEvent, summary: str) -> SyncResult:
        del self, evt, summary
        calls.append(1)
        await asyncio.sleep(0.05)
        return SyncResult(target="telegram", ok=True, detail="sent", message_id="1")

    monkeypatch.setattr(tg_mod.TelegramOutbound, "sync_completion", _fake_sync)

    results = await asyncio.gather(
        engine.complete_event(event.id, summary="A", sync=["telegram"]),
        engine.complete_event(event.id, summary="B", sync=["telegram"]),
    )
    assert len(calls) == 1
    already_flags = sorted(already for _, already in results)
    assert already_flags == [False, True]
    summaries = {completion.summary for completion, _ in results}
    assert len(summaries) == 1
    assert summaries.pop() in {"A", "B"}


@pytest.mark.asyncio
async def test_telegram_outbound_uses_store_secret(engine: Engine) -> None:
    from pboy.channels.telegram import TELEGRAM_SECRET_KEY
    from pboy.outbound.telegram import TelegramOutbound

    engine.settings.telegram_bot_token = ""
    await engine.store.put_secret(TELEGRAM_SECRET_KEY, "stored-out-token")
    event = await _seed_telegram_event(engine)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
        )

    outbound = TelegramOutbound(
        engine.store,
        engine.settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_base="https://api.telegram.org",
    )
    try:
        result = await outbound.sync_completion(event, "hi")
    finally:
        await outbound.aclose()
    assert result.ok is True
    assert any("stored-out-token" in u for u in seen)


@pytest.mark.asyncio
async def test_daemon_complete_rejects_remote_peer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pboy.core.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None)
    settings = Settings()
    app_api = create_app(
        db_path=tmp_path / "local-complete.db",
        secret_key="local-complete-key",
        settings=settings,
    )
    eng: Engine = app_api.state.engine
    remote = ASGITransport(app=app_api, client=("203.0.113.10", 50000))
    loopback = ASGITransport(app=app_api)
    async with app_api.router.lifespan_context(app_api):
        async with AsyncClient(transport=remote, base_url="http://127.0.0.1") as client:
            event = await _seed_telegram_event(eng)
            denied = await client.post(
                f"/v1/events/{event.id}/complete",
                json={"summary": "nope", "sync": []},
            )
            assert denied.status_code == 403

        async with AsyncClient(transport=loopback, base_url="http://127.0.0.1") as client:
            not_found = await client.post(
                "/v1/events/evt_does_not_exist/complete",
                json={"summary": "x", "sync": []},
            )
            assert not_found.status_code == 404
            assert not_found.json()["detail"] == "Event 'evt_does_not_exist' not found"

            local_event = await _seed_telegram_event(eng)
            local_ok = await client.post(
                f"/v1/events/{local_event.id}/complete",
                json={"summary": "local ok", "sync": []},
            )
            assert local_ok.status_code == 200


@pytest.mark.asyncio
async def test_cli_event_complete_via_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket
    import threading
    import time

    import uvicorn

    import pboy.core.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None)
    settings = Settings()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    application = create_app(
        db_path=tmp_path / "cli-daemon-complete.db",
        secret_key="cli-daemon-complete-key",
        settings=settings,
    )
    config = uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("uvicorn failed to start")

    try:
        eng: Engine = application.state.engine
        # lifespan may not have run in Server the same way — start engine manually if needed
        if not getattr(eng.store, "_db", None):
            await eng.start()
        event = await _seed_telegram_event(eng)

        monkeypatch.setenv("PB_DAEMON_URL", f"http://127.0.0.1:{port}")
        runner = CliRunner()
        res = runner.invoke(
            app,
            ["event", "complete", event.id, "--summary", "via-daemon"],
        )
        assert res.exit_code == 0, res.output
        assert "via-daemon" in res.output
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_cli_event_complete_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PB_DAEMON_URL", raising=False)
    monkeypatch.setenv("PB_DATABASE_URL", str(tmp_path / "cli-complete.db"))
    monkeypatch.setenv("PB_SECRET_KEY", "cli-complete-key!!")
    import pboy.core.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None)

    async def _seed() -> str:
        store = SQLiteStore(
            db_path=tmp_path / "cli-complete.db",
            secret_key="cli-complete-key!!",
        )
        await store.init_db()
        try:
            event = CloudEvent(
                source="jira",
                type="com.atlassian.jira.issue.created",
                data={"entity_key": "X-1", "summary": "s"},
            )
            await store.create_event(event)
            return event.id
        finally:
            await store.close()

    import asyncio

    event_id = asyncio.run(_seed())
    runner = CliRunner()
    res = runner.invoke(
        app,
        ["event", "complete", event_id, "--summary", "CLI done"],
    )
    assert res.exit_code == 0, res.output
    assert "completed" in res.output.lower()

    again = runner.invoke(app, ["event", "complete", event_id, "--summary", "x"])
    assert again.exit_code == 0
    assert "already completed" in again.output.lower()

    get_res = runner.invoke(app, ["event", "get", event_id])
    assert get_res.exit_code == 0, get_res.output
    assert "CLI done" in get_res.output
    assert "completion" in get_res.output
