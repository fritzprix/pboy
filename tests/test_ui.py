"""Embedded daemon dashboard (M5.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from pboy.channels.telegram import TelegramChannel
from pboy.channels.telegram_app import TelegramAppService
from pboy.cli.main import app
from pboy.core.config import Settings
from pboy.core.engine import Engine
from pboy.daemon.app import create_app
from pboy.models.execution import ExecutionData
from pboy.models.hook import HookCreate
from pboy.ui.agent_brief import render_agent_brief


def _mock_telegram_http(
    *,
    token_ok: bool = True,
    sent_messages: list[dict[str, Any]] | None = None,
) -> httpx.AsyncClient:
    """httpx client that never touches api.telegram.org."""
    sent = sent_messages if sent_messages is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            if not token_ok:
                return httpx.Response(
                    401, json={"ok": False, "description": "Unauthorized"}
                )
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 1, "is_bot": True, "username": "t"}}
            )
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404, json={"ok": False})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _register_mock_telegram(
    application: Any,
    *,
    token_ok: bool = True,
    sent_messages: list[dict[str, Any]] | None = None,
    data_dir: Path | None = None,
) -> TelegramChannel:
    eng: Engine = application.state.engine
    channel = TelegramChannel(
        eng.process_event,
        eng.store,
        Settings(data_dir=data_dir) if data_dir is not None else Settings(),
        client=_mock_telegram_http(token_ok=token_ok, sent_messages=sent_messages),
        poll_timeout=1,
    )
    application.state.channel_registry.register(channel)
    return channel


async def _pairing_code_via_inbound(
    adapter: TelegramChannel,
    sent_messages: list[dict[str, Any]],
    *,
    chat_id: str = "777888",
    from_id: str = "777888",
) -> str:
    """Drive the real pairing path: start_pairing → inbound /start → parse code."""
    await adapter.start_pairing()
    await adapter.handle_update(
        {
            "update_id": 9001,
            "message": {
                "message_id": 1,
                "text": "/start",
                "from": {"id": int(from_id), "username": "op"},
                "chat": {"id": int(chat_id), "type": "private"},
            },
        }
    )
    assert sent_messages, "expected pairing sendMessage"
    match = re.search(r"(\d{6})", sent_messages[-1]["text"])
    assert match is not None
    return match.group(1)


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
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
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
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        dash = await client.get("/ui")
        assert dash.status_code == 200
        assert "No hooks registered." in dash.text
        assert "No events yet." in dash.text
        assert "No executions yet." in dash.text
        assert 'data-copy-agent-brief' in dash.text
        assert "Pageboy agent brief" in dash.text

        brief = await client.get("/ui/agent-brief.txt")
        assert brief.status_code == 200
        assert "text/plain" in brief.headers["content-type"]
        assert brief.text == render_agent_brief("http://127.0.0.1")
        assert "{base_url}" not in brief.text

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
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
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
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
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
async def test_ui_remote_peer_rejected(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-remote.db",
        secret_key="ui-remote-key",
        enable_ui=True,
    )
    remote = ASGITransport(app=application, client=("203.0.113.10", 50000))
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=remote, base_url="http://127.0.0.1") as client,
    ):
        denied = await client.get("/ui")
        assert denied.status_code == 403


def test_cli_ui_help(cli_runner: CliRunner) -> None:
    from tests.conftest import strip_ansi

    res = cli_runner.invoke(app, ["ui", "--help"])
    assert res.exit_code == 0
    assert "dashboard" in strip_ansi(res.output).lower()


def test_cli_daemon_ui_flag_help(cli_runner: CliRunner) -> None:
    from tests.conftest import strip_ansi

    res = cli_runner.invoke(app, ["daemon", "--help"])
    assert res.exit_code == 0
    plain = strip_ansi(res.output)
    assert "--ui" in plain


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
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
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
        assert 'name="secret__app_token"' in body
        assert 'name="secret__api_id"' in body
        assert 'name="secret__api_hash"' in body
        assert 'id="telegram-bot"' in body
        assert 'data-bot-step="token"' in body
        assert 'action="/ui/settings/channels/telegram/enable"' not in body
        assert 'action="/ui/settings/channels/telegram/pair-start"' not in body
        assert 'id="telegram-app-api"' in body
        assert 'data-app-step="credentials"' in body
        assert 'action="/ui/settings/channels/telegram/app-login-start"' not in body
        assert "my.telegram.org" in body


class _SessionPasswordNeededError(Exception):
    """Stand-in matched by class name like Telethon's ``SessionPasswordNeededError``."""


_SessionPasswordNeededError.__name__ = "SessionPasswordNeededError"


def _fake_telethon_client(*, needs_password: bool) -> Any:
    client = MagicMock()
    client.session.save.return_value = "temp-session"
    client.send_code_request = AsyncMock(return_value=SimpleNamespace(phone_code_hash="h"))

    async def sign_in(**kwargs: Any) -> None:
        if "code" in kwargs and needs_password:
            raise _SessionPasswordNeededError()

    client.sign_in = AsyncMock(side_effect=sign_in)
    client.get_me = AsyncMock(return_value=SimpleNamespace(id=7, username="alice"))
    client.disconnect = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_ui_settings_telegram_app_api_sequential_steps(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-app-flow.db",
        secret_key="ui-app-flow-key",
        enable_ui=True,
    )
    fake = _fake_telethon_client(needs_password=True)

    async def factory(*_a: Any, **_k: Any) -> Any:
        return fake

    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        eng: Engine = application.state.engine
        application.state.telegram_app = TelegramAppService(
            store=eng.store, client_factory=factory
        )

        async def page() -> str:
            return (await client.get("/ui/settings")).text

        async def post(path: str, **data: str) -> None:
            res = await client.post(path, data=data, follow_redirects=False)
            assert res.status_code == 303

        secret = "/ui/settings/channels/telegram/secret"
        body = await page()
        assert 'data-app-step="credentials"' in body
        assert "app-login-start" not in body

        await post(secret, secret_name="api_id", value="12345678")
        assert 'data-app-step="credentials"' in await page()

        await post(secret, secret_name="api_hash", value="abcdef0123456789")
        body = await page()
        assert 'data-app-step="phone"' in body
        assert "✓ App API 등록됨" in body
        assert "app-login-verify" not in body

        await post("/ui/settings/channels/telegram/app-login-start", phone="+821012345678")
        body = await page()
        assert 'data-app-step="code"' in body
        assert "+821******678" in body
        assert 'action="/ui/settings/channels/telegram/app-login-start"' not in body
        assert 'name="password"' not in body

        await post("/ui/settings/channels/telegram/app-login-verify", code="12345")
        body = await page()
        assert 'data-app-step="password"' in body
        assert 'action="/ui/settings/channels/telegram/app-login-password"' in body
        assert "app-login-verify" not in body

        await post("/ui/settings/channels/telegram/app-login-password", password="pw")
        body = await page()
        assert 'data-app-step="connected"' in body
        assert "계정 연결됨" in body

        # Clearing the App API hash drops the session: back to step 1, not "connected".
        await post(secret, secret_name="api_hash", value="")
        assert 'data-app-step="credentials"' in await page()
        assert await eng.store.get_secret("telegram.app_session") == ""


@pytest.mark.asyncio
async def test_ui_settings_secret_group_saves_together_and_keeps_blanks(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-secret-group.db",
        secret_key="ui-secret-group-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        eng: Engine = application.state.engine
        url = "/ui/settings/channels/slack/secrets"

        res = await client.post(
            url,
            data={"secret__bot_token": "xoxb-1", "secret__app_token": "xapp-1"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert "#channel-slack" in res.headers["location"]
        assert await eng.store.get_secret("slack.bot_token") == "xoxb-1"
        assert await eng.store.get_secret("slack.app_token") == "xapp-1"

        # A blank input keeps the stored value instead of clearing it.
        await client.post(
            url,
            data={"secret__bot_token": "xoxb-2", "secret__app_token": ""},
            follow_redirects=False,
        )
        assert await eng.store.get_secret("slack.bot_token") == "xoxb-2"
        assert await eng.store.get_secret("slack.app_token") == "xapp-1"

        empty = await client.post(
            url,
            data={"secret__bot_token": " ", "secret__app_token": ""},
            follow_redirects=False,
        )
        assert "err=" in empty.headers["location"]

        app_keys = await client.post(
            "/ui/settings/channels/telegram/secrets",
            data={"secret__api_id": "12345678", "secret__api_hash": "abcdef"},
            follow_redirects=False,
        )
        assert "#telegram-app-api" in app_keys.headers["location"]
        body = (await client.get("/ui/settings")).text
        assert 'data-app-step="phone"' in body
        # api_id is not a secret: visible while typing; the hash stays masked.
        assert 'type="text"\n    name="secret__api_id"' in body
        assert 'type="password"\n    name="secret__api_hash"' in body
        # Saved values are not required again; saved optional keys offer a clear box.
        assert 'name="clear__api_hash"' in body
        assert 'name="clear__bot_token"' not in body
        slack_form = body[body.index('action="/ui/settings/channels/slack/secrets"') :]
        slack_form = slack_form[: slack_form.index("</form>")]
        assert "required" not in slack_form

        # Clearing api_hash drops the App API session and returns to step 1.
        cleared = await client.post(
            "/ui/settings/channels/telegram/secrets",
            data={"secret__api_id": "", "secret__api_hash": "", "clear__api_hash": "1"},
            follow_redirects=False,
        )
        assert "saved=" in cleared.headers["location"]
        assert await eng.store.get_secret("telegram.api_hash") == ""
        assert await eng.store.get_secret("telegram.api_id") == "12345678"
        body = (await client.get("/ui/settings")).text
        assert 'data-app-step="credentials"' in body


@pytest.mark.asyncio
async def test_ui_settings_telegram_app_cancel_returns_to_phone(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-app-cancel.db",
        secret_key="ui-app-cancel-key",
        enable_ui=True,
    )
    fake = _fake_telethon_client(needs_password=False)

    async def factory(*_a: Any, **_k: Any) -> Any:
        return fake

    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        eng: Engine = application.state.engine
        await eng.store.put_secret("telegram.api_id", "1")
        await eng.store.put_secret("telegram.api_hash", "h")
        application.state.telegram_app = TelegramAppService(
            store=eng.store, client_factory=factory
        )
        await client.post(
            "/ui/settings/channels/telegram/app-login-start", data={"phone": "+821012345678"}
        )
        assert 'data-app-step="code"' in (await client.get("/ui/settings")).text

        await client.post("/ui/settings/channels/telegram/app-login-cancel")
        assert 'data-app-step="phone"' in (await client.get("/ui/settings")).text


@pytest.mark.asyncio
async def test_api_key_change_cancels_pending_login(tmp_path: Path) -> None:
    """HTTP API / CLI key changes must drop the in-memory login, not only the UI form."""
    application = create_app(
        db_path=tmp_path / "ui-app-api-key.db",
        secret_key="ui-app-api-key",
        enable_ui=True,
    )
    fake = _fake_telethon_client(needs_password=False)

    async def factory(*_a: Any, **_k: Any) -> Any:
        return fake

    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        eng: Engine = application.state.engine
        await eng.store.put_secret("telegram.api_id", "1")
        await eng.store.put_secret("telegram.api_hash", "h")
        service = TelegramAppService(store=eng.store, client_factory=factory)
        application.state.telegram_app = service
        await service.request_login_code("+821012345678")
        assert service.login_step == "code"

        res = await client.put(
            "/v1/channels/telegram/secret",
            json={"secret_name": "api_hash", "value": "new-hash"},
        )
        assert res.status_code == 200
        assert service.login_step == "idle"
        assert 'data-app-step="phone"' in (await client.get("/ui/settings")).text


@pytest.mark.asyncio
async def test_ui_settings_telegram_bot_sequential_steps(tmp_path: Path) -> None:
    """Bot card: token → enable → pair → protected (only the current step open)."""
    application = create_app(
        db_path=tmp_path / "ui-bot-flow.db",
        secret_key="ui-bot-flow-key",
        enable_ui=True,
        settings=Settings(data_dir=tmp_path / "ui-bot-flow-data"),
    )
    sent: list[dict[str, Any]] = []
    _register_mock_telegram(application, sent_messages=sent, data_dir=tmp_path / "ui-bot-flow-data")
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        eng: Engine = application.state.engine

        async def page() -> str:
            return (await client.get("/ui/settings")).text

        body = await page()
        assert 'data-bot-step="token"' in body
        assert 'action="/ui/settings/channels/telegram/enable"' not in body
        assert "pair-start" not in body
        assert "이렇게 준비하세요" not in body.split('id="channel-telegram"')[1].split(
            'id="channel-slack"'
        )[0]

        save = await client.post(
            "/ui/settings/channels/telegram/secret",
            data={"secret_name": "bot_token", "value": "123:TELEGRAM-TEST-TOKEN"},
            follow_redirects=False,
        )
        assert save.status_code == 303
        assert "telegram.bot_token" in save.headers["location"]
        assert "#telegram-bot" in save.headers["location"]
        assert await eng.store.get_secret("telegram.bot_token") == "123:TELEGRAM-TEST-TOKEN"

        body = await page()
        assert "123:TELEGRAM-TEST-TOKEN" not in body
        assert 'data-bot-step="enable"' in body
        assert "✓ 봇 토큰 저장됨" in body
        assert 'action="/ui/settings/channels/telegram/enable"' in body
        assert "pair-start" not in body
        assert "아무 채팅이나" not in body

        enable = await client.post(
            "/ui/settings/channels/telegram/enable",
            follow_redirects=False,
        )
        assert enable.status_code == 303
        assert "#telegram-bot" in enable.headers["location"]
        status = await application.state.channel_registry.list_status()
        tg = next(r for r in status if r["name"] == "telegram")
        assert tg["enabled"] is True
        assert tg["running"] is True

        body = await page()
        assert 'data-bot-step="pair"' in body
        assert "✓ 켜짐" in body
        assert "아무 채팅이나" in body
        assert 'role="alert"' in body
        assert 'action="/ui/settings/channels/telegram/pair-start"' in body
        assert 'action="/ui/settings/channels/telegram/pair-verify"' not in body
        assert "1단계 · 대화방" not in body

        pair_start = await client.post(
            "/ui/settings/channels/telegram/pair-start",
            follow_redirects=False,
        )
        assert pair_start.status_code == 303
        assert "#telegram-bot" in pair_start.headers["location"]
        adapter = application.state.channel_registry.get("telegram")
        assert isinstance(adapter, TelegramChannel)
        assert adapter.pairing_active is True

        body = await page()
        assert 'data-bot-step="pair"' in body
        assert 'action="/ui/settings/channels/telegram/pair-verify"' in body

        code = await _pairing_code_via_inbound(adapter, sent)
        pair_verify = await client.post(
            "/ui/settings/channels/telegram/pair-verify",
            data={"code": code},
            follow_redirects=False,
        )
        assert pair_verify.status_code == 303
        assert "telegram.allowlist" in pair_verify.headers["location"]
        assert "#telegram-bot" in pair_verify.headers["location"]
        assert await eng.store.get_secret("telegram.allowlist") == "777888"

        body = await page()
        assert 'data-bot-step="protected"' in body
        assert "✓ Allowlist 보호 중" in body
        assert "pboy hook register" in body
        assert "아무 채팅이나" not in body


@pytest.mark.asyncio
async def test_ui_settings_telegram_bot_bad_token_stays_on_enable(
    tmp_path: Path,
) -> None:
    """getMe 401 keeps the wizard on enable with the auth error — not pair."""
    application = create_app(
        db_path=tmp_path / "ui-bot-bad.db",
        secret_key="ui-bot-bad-key",
        enable_ui=True,
        settings=Settings(data_dir=tmp_path / "ui-bot-bad-data"),
    )
    _register_mock_telegram(
        application, token_ok=False, data_dir=tmp_path / "ui-bot-bad-data"
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        await client.post(
            "/ui/settings/channels/telegram/secret",
            data={"secret_name": "bot_token", "value": "123:BAD-TOKEN"},
            follow_redirects=False,
        )
        enable = await client.post(
            "/ui/settings/channels/telegram/enable",
            follow_redirects=False,
        )
        assert enable.status_code == 303
        assert "err=" in enable.headers["location"]

        body = (await client.get("/ui/settings")).text
        assert 'data-bot-step="enable"' in body
        assert "거부" in body
        assert "pair-start" not in body
        status = await application.state.channel_registry.list_status()
        tg = next(r for r in status if r["name"] == "telegram")
        assert tg["enabled"] is True
        assert tg["running"] is False


@pytest.mark.asyncio
async def test_ui_settings_save_telegram_secret_and_enable(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-save.db",
        secret_key="ui-save-key",
        enable_ui=True,
        settings=Settings(data_dir=tmp_path / "ui-save-data"),
    )
    sent: list[dict[str, Any]] = []
    _register_mock_telegram(application, sent_messages=sent, data_dir=tmp_path / "ui-save-data")
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
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

        adapter = application.state.channel_registry.get("telegram")
        assert isinstance(adapter, TelegramChannel)
        code = await _pairing_code_via_inbound(adapter, sent)
        pair_verify = await client.post(
            "/ui/settings/channels/telegram/pair-verify",
            data={"code": code},
            follow_redirects=False,
        )
        assert pair_verify.status_code == 303
        assert "telegram.allowlist" in pair_verify.headers["location"]
        assert await eng.store.get_secret("telegram.allowlist") == "777888"


@pytest.mark.asyncio
async def test_ui_loopback_mutation_allowed(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-local.db",
        secret_key="ui-local-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
        saved = await client.post(
            "/ui/settings/harness-secret",
            data={"key": "jira.api_token", "value": "ATATT-local"},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        eng: Engine = application.state.engine
        assert await eng.store.get_secret("jira.api_token") == "ATATT-local"


@pytest.mark.asyncio
async def test_ui_csrf_block(tmp_path: Path) -> None:
    application = create_app(
        db_path=tmp_path / "ui-csrf.db",
        secret_key="ui-csrf-key",
        enable_ui=True,
    )
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
    ):
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
        AsyncClient(transport=transport, base_url="http://127.0.0.1") as client,
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

