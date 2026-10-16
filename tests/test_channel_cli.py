"""CLI + daemon API for channel enable/disable/set-secret."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from pboy.channels.config import CHANNELS_ENABLED_SECRET
from pboy.channels.telegram import TELEGRAM_SECRET_KEY
from pboy.cli.main import app
from pboy.core.config import Settings
from pboy.daemon.app import create_app
from pboy.store.sqlite import SQLiteStore


def test_channel_cli_roundtrip_local_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PB_CHANNELS_ENABLED", raising=False)
    db = tmp_path / "cli-ch.db"
    monkeypatch.setenv("PB_DATABASE_URL", str(db))
    monkeypatch.setenv("PB_SECRET_KEY", "cli-channel-secret-key!!")

    import pboy.core.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None)

    runner = CliRunner()
    set_res = runner.invoke(
        app,
        ["channel", "set-secret", "telegram", "--value", "bot-token-123"],
    )
    assert set_res.exit_code == 0, set_res.output
    assert TELEGRAM_SECRET_KEY in set_res.output

    en_res = runner.invoke(app, ["channel", "enable", "telegram"])
    assert en_res.exit_code == 0, en_res.output

    list_res = runner.invoke(app, ["channel", "list"])
    assert list_res.exit_code == 0, list_res.output
    assert "telegram" in list_res.output
    assert "enabled" in list_res.output
    assert "secret=yes" in list_res.output

    async def _check_enabled() -> None:
        store = SQLiteStore(db_path=db, secret_key="cli-channel-secret-key!!")
        await store.init_db()
        try:
            assert await store.get_secret(TELEGRAM_SECRET_KEY) == "bot-token-123"
            assert await store.get_secret(CHANNELS_ENABLED_SECRET) == "telegram"
        finally:
            await store.close()

    asyncio.run(_check_enabled())

    dis_res = runner.invoke(app, ["channel", "disable", "telegram"])
    assert dis_res.exit_code == 0, dis_res.output

    async def _check_disabled() -> None:
        store = SQLiteStore(db_path=db, secret_key="cli-channel-secret-key!!")
        await store.init_db()
        try:
            assert await store.get_secret(CHANNELS_ENABLED_SECRET) == ""
        finally:
            await store.close()

    asyncio.run(_check_disabled())


@pytest.mark.asyncio
async def test_daemon_channel_api_set_secret_and_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PB_CHANNELS_ENABLED", raising=False)
    import pboy.core.config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None)
    settings = Settings()

    app_api = create_app(
        db_path=tmp_path / "api-ch.db",
        secret_key="api-ch-key",
        settings=settings,
    )
    transport = ASGITransport(app=app_api)
    async with (
        app_api.router.lifespan_context(app_api),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        put = await client.put(
            "/v1/channels/telegram/secret",
            json={"value": "api-bot-token"},
        )
        assert put.status_code == 200
        assert put.json()["key"] == TELEGRAM_SECRET_KEY

        en = await client.post("/v1/channels/fake/enable")
        assert en.status_code == 200
        assert en.json()["running"] is True

        listed = await client.get("/v1/channels")
        assert listed.status_code == 200
        body = listed.json()
        by_name = {c["name"]: c for c in body["channels"]}
        assert by_name["fake"]["enabled"] is True
        assert by_name["fake"]["running"] is True
        assert by_name["telegram"]["has_secret"] is True

        put_allow = await client.put(
            "/v1/channels/telegram/secret",
            json={"value": "123456,-100999", "secret_name": "allowlist"},
        )
        assert put_allow.status_code == 200
        assert put_allow.json()["key"] == "telegram.allowlist"

        clear_allow = await client.put(
            "/v1/channels/telegram/secret",
            json={"value": "", "secret_name": "allowlist"},
        )
        assert clear_allow.status_code == 200
        assert clear_allow.json()["key"] == "telegram.allowlist"

        # Required secret still rejects empty value
        empty_req = await client.put(
            "/v1/channels/telegram/secret",
            json={"value": "   ", "secret_name": "bot_token"},
        )
        assert empty_req.status_code == 400

        dis = await client.post("/v1/channels/fake/disable")
        assert dis.status_code == 200
        assert dis.json()["running"] is False
