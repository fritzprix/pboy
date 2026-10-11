"""Embedded daemon dashboard (M5.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from pboy.cli.main import app
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.daemon.app import create_app
from pboy.models.execution import ExecutionData
from pboy.models.hook import HookCreate


@pytest.mark.asyncio
async def test_ui_disabled_by_default(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-off.db",
        secret_key="ui-off-key",
        enable_ui=False,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        res = await client.get("/ui")
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_ui_empty_states(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-empty.db",
        secret_key="ui-empty-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        dash = await client.get("/ui")
        assert dash.status_code == 200
        assert "No hooks registered." in dash.text
        assert "No events yet." in dash.text
        assert "No executions yet." in dash.text

        assert "No hooks registered." in (await client.get("/ui/partials/hooks")).text
        assert "No events yet." in (await client.get("/ui/partials/events")).text
        assert "No executions yet." in (await client.get("/ui/partials/executions")).text


@pytest.mark.asyncio
async def test_ui_dashboard_and_partials(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-on.db",
        secret_key="ui-on-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        eng: Engine = application.state.engine
        await eng.store.create_hook(
            HookCreate(
                name="ui-echo",
                topic="jira:issue.created",
                filter='.data.priority == "High"',
                exec="echo ui-ok",
                concurrency=2,
                timeout=30,
            )
        )
        event, _matched, _execs = await eng.process_event(
            {
                "webhookEvent": "jira:issue_created",
                "issue": {
                    "id": "1",
                    "key": "UI-1",
                    "fields": {
                        "summary": "ui",
                        "priority": {"name": "High"},
                        "project": {"key": "UI"},
                        "creator": {"accountId": "u1", "displayName": "Ui"},
                        "labels": [],
                    },
                },
            },
            "jira",
            dispatch=False,
        )
        await eng.store.record_execution(
            ExecutionData(
                hook_name="ui-echo",
                event_id=event.id,
                status="completed",
                exit_code=0,
                stdout="ui-ok\n",
            )
        )

        dash = await client.get("/ui")
        assert dash.status_code == 200
        assert "text/html" in dash.headers["content-type"]
        assert "Pageboy" in dash.text
        assert "ui-echo" in dash.text
        assert event.id in dash.text
        assert "/ui/static/htmx.min.js" in dash.text
        assert "cdn.jsdelivr.net" not in dash.text

        css = await client.get("/ui/static/dashboard.css")
        assert css.status_code == 200
        assert "font-family" in css.text

        htmx = await client.get("/ui/static/htmx.min.js")
        assert htmx.status_code == 200
        assert len(htmx.content) > 1000

        hooks = await client.get("/ui/partials/hooks")
        assert hooks.status_code == 200
        assert "ui-echo" in hooks.text
        assert "jira:issue.created" in hooks.text
        assert ".data.priority" in hooks.text
        assert ">2<" in hooks.text

        events = await client.get("/ui/partials/events")
        assert events.status_code == 200
        assert event.id in events.text

        executions = await client.get("/ui/partials/executions")
        assert executions.status_code == 200
        assert "ui-echo" in executions.text
        assert "completed" in executions.text


@pytest.mark.asyncio
async def test_ui_escapes_html_in_hook_exec(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-xss.db",
        secret_key="ui-xss-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        eng: Engine = application.state.engine
        await eng.store.create_hook(
            HookCreate(
                name="xss-hook",
                topic="jira:issue.created",
                exec='echo "<script>alert(1)</script>"',
                timeout=30,
            )
        )
        body = (await client.get("/ui/partials/hooks")).text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


@pytest.mark.asyncio
async def test_ui_non_loopback_requires_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pboy.core.config as config_mod

    monkeypatch.setenv("PB_AUTH_TOKEN", "ui-secret")
    monkeypatch.setattr(config_mod, "_settings", None)
    settings = Settings(auth_token="ui-secret")

    application = create_app(
        db_path=tmp_path / "ui-auth.db",
        secret_key="ui-auth-key",
        settings=settings,
        enable_ui=True,
    )
    remote = ASGITransport(app=application, client=("203.0.113.10", 50000))
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=remote, base_url="http://test") as client,
    ):
        denied = await client.get("/ui")
        assert denied.status_code == 401

        ok_header = await client.get("/ui", headers={"X-PB-Token": "ui-secret"})
        assert ok_header.status_code == 200
        assert "Pageboy" in ok_header.text

        ok_query = await client.get("/ui", params={"token": "ui-secret"})
        assert ok_query.status_code == 200
        assert 'hx-headers=\'{"X-PB-Token": "ui-secret"}\'' in ok_query.text


def test_cli_ui_help(cli_runner: CliRunner) -> None:
    res = cli_runner.invoke(app, ["ui", "--help"])
    assert res.exit_code == 0
    assert "dashboard" in res.output.lower()


def test_cli_daemon_ui_flag_help(cli_runner: CliRunner) -> None:
    res = cli_runner.invoke(app, ["daemon", "--help"])
    assert res.exit_code == 0
    assert "--ui" in res.output


def test_cli_ui_invokes_run_daemon(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ui`` command imports ``run_daemon`` inside the handler."""
    from unittest.mock import MagicMock

    mock = MagicMock()
    monkeypatch.setattr("pboy.daemon.server.run_daemon", mock)
    res = cli_runner.invoke(app, ["ui", "--port", "8123"])
    assert res.exit_code == 0
    mock.assert_called_once()
    kwargs = mock.call_args.kwargs
    assert kwargs["ui"] is True
    assert kwargs["port"] == 8123
    assert kwargs["host"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_ui_settings_guides_and_tooltips(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-set.db",
        secret_key="ui-set-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        page = await client.get("/ui/settings")
        assert page.status_code == 200
        body = page.text
        assert "https://t.me/BotFather" in body
        assert "https://api.slack.com/apps" in body
        assert "https://id.atlassian.com/manage-profile/security/api-tokens" in body
        assert "BotFather가 /newbot" in body  # tooltip text
        assert "xoxb-" in body
        assert "xapp-" in body
        assert "깨우는 채널" in body
        assert "훅이 띄운 프로그램" in body
        assert 'name="secret_name" value="bot_token"' in body
        assert 'name="secret_name" value="app_token"' in body


@pytest.mark.asyncio
async def test_ui_settings_save_telegram_secret_and_enable(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-save.db",
        secret_key="ui-save-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        save = await client.post(
            "/ui/settings/channels/telegram/secret",
            data={"secret_name": "bot_token", "value": "123:TELEGRAM-TEST-TOKEN"},
            follow_redirects=False,
        )
        assert save.status_code == 303
        assert "telegram.bot_token" in save.headers["location"]

        eng: Engine = application.state.engine
        stored = await eng.store.get_secret("telegram.bot_token")
        assert stored == "123:TELEGRAM-TEST-TOKEN"

        # Secret must never be echoed back into the settings HTML.
        view = await client.get("/ui/settings")
        assert "123:TELEGRAM-TEST-TOKEN" not in view.text
        assert "저장됨" in view.text

        enable = await client.post(
            "/ui/settings/channels/telegram/enable",
            follow_redirects=False,
        )
        assert enable.status_code == 303
        status = await application.state.channel_registry.list_status()
        tg = next(r for r in status if r["name"] == "telegram")
        assert tg["enabled"] is True


@pytest.mark.asyncio
async def test_ui_login_cookie_required_for_mutation_when_auth_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pboy.core.config as config_mod

    monkeypatch.setenv("PB_AUTH_TOKEN", "ui-form-secret")
    monkeypatch.setattr(config_mod, "_settings", None)
    settings = Settings(auth_token="ui-form-secret")

    application = create_app(
        db_path=tmp_path / "ui-login.db",
        secret_key="ui-login-key",
        settings=settings,
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        denied = await client.post(
            "/ui/settings/channels/telegram/secret",
            data={"secret_name": "bot_token", "value": "should-fail"},
            follow_redirects=False,
        )
        assert denied.status_code == 401

        bad = await client.post(
            "/ui/login",
            data={"password": "wrong"},
            follow_redirects=False,
        )
        assert bad.status_code == 303
        assert "error=1" in bad.headers["location"]

        ok = await client.post(
            "/ui/login",
            data={"password": "ui-form-secret"},
            follow_redirects=False,
        )
        assert ok.status_code == 303
        assert "pb_ui_session" in ok.headers.get("set-cookie", "")
        session = ok.cookies["pb_ui_session"]
        assert session != "ui-form-secret"
        assert "." in session

        client.cookies.set("pb_ui_session", session)
        saved = await client.post(
            "/ui/settings/harness-secret",
            data={"key": "jira.api_token", "value": "ATATT-test"},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        eng: Engine = application.state.engine
        assert await eng.store.get_secret("jira.api_token") == "ATATT-test"

        logout = await client.post("/ui/logout", follow_redirects=False)
        assert logout.status_code == 303
        client.cookies.clear()
        denied_again = await client.post(
            "/ui/settings/harness-secret",
            data={"key": "jira.email", "value": "a@b.c"},
            follow_redirects=False,
        )
        assert denied_again.status_code == 401


@pytest.mark.asyncio
async def test_ui_login_page_and_csrf_block(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-csrf.db",
        secret_key="ui-csrf-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        login = await client.get("/ui/login")
        assert login.status_code == 200
        assert "PB_AUTH_TOKEN" in login.text

        blocked = await client.post(
            "/ui/settings/channels/telegram/secret",
            data={"secret_name": "bot_token", "value": "csrf-attempt"},
            headers={"Sec-Fetch-Site": "cross-site"},
            follow_redirects=False,
        )
        assert blocked.status_code == 403

        bad_origin = await client.post(
            "/ui/settings/harness-secret",
            data={"key": "jira.api_token", "value": "x"},
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
        )
        assert bad_origin.status_code == 403


@pytest.mark.asyncio
async def test_ui_disable_channel_and_harness_errors(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-dis.db",
        secret_key="ui-dis-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await client.post(
            "/ui/settings/channels/telegram/secret",
            data={"secret_name": "bot_token", "value": "123:TOKEN"},
            follow_redirects=False,
        )
        await client.post(
            "/ui/settings/channels/telegram/enable",
            follow_redirects=False,
        )
        disable = await client.post(
            "/ui/settings/channels/telegram/disable",
            follow_redirects=False,
        )
        assert disable.status_code == 303
        assert "disabled" in disable.headers["location"]
        status = await application.state.channel_registry.list_status()
        tg = next(r for r in status if r["name"] == "telegram")
        assert tg["enabled"] is False

        unknown = await client.post(
            "/ui/settings/harness-secret",
            data={"key": "not.allowed", "value": "x"},
            follow_redirects=False,
        )
        assert unknown.status_code == 303
        assert "err=" in unknown.headers["location"]


def test_mint_and_verify_ui_session() -> None:
    from pboy.daemon.auth import mint_ui_session, verify_ui_session

    token = "s3cret"
    cookie = mint_ui_session(token)
    assert verify_ui_session(cookie, token) is True
    assert verify_ui_session(cookie, "other") is False
    assert verify_ui_session("not-a-cookie", token) is False
