"""Mount HTMX dashboard + settings forms on the daemon FastAPI app."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pboy.channels.registry import ChannelRegistry
from pboy.channels.telegram_app import (
    TELEGRAM_APP_SESSION_SECRET_KEY,
    TelegramAppService,
)
from pboy.core.engine import Engine
from pboy.ui.agent_brief import render_agent_brief
from pboy.ui.guides import (
    CHANNEL_GUIDES,
    HARNESS_SECRET_FIELDS,
    JIRA_INFO,
    TELEGRAM_APP_GUIDE,
)

_UI_ROOT = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_UI_ROOT / "templates"))

_HARNESS_KEYS = frozenset(str(f["key"]) for f in HARNESS_SECRET_FIELDS)


_APP_CREDENTIAL_NAMES = frozenset({"api_id", "api_hash"})
_APP_ANCHOR = "#telegram-app-api"


def _app_connected_redirect(info: dict[str, Any]) -> RedirectResponse:
    who = info.get("username") or info.get("user_id") or "ok"
    return RedirectResponse(
        f"/ui/settings?saved={quote(f'telegram.app_session (계정 연결 완료: {who})')}",
        status_code=303,
    )


def _telegram_app_step(secret_flags: dict[str, bool], *, login_step: str) -> str:
    """App API wizard step: credentials → phone → code (→ password) → connected."""
    if not secret_flags.get("api_id") or not secret_flags.get("api_hash"):
        return "credentials"
    if secret_flags.get("app_session"):
        return "connected"
    if login_step in ("code", "password"):
        return login_step
    return "phone"


async def _channel_rows(
    registry: ChannelRegistry,
    *,
    engine: Engine,
    telegram_app: TelegramAppService | None = None,
) -> list[dict[str, Any]]:
    status_rows = await registry.list_status()
    by_name = {str(r["name"]): r for r in status_rows}
    rows: list[dict[str, Any]] = []
    for guide in CHANNEL_GUIDES:
        name = guide["name"]
        st = by_name.get(name, {})
        secret_flags: dict[str, bool] = {}
        for field in guide["fields"]:
            full_key = f"{name}.{field['secret_name']}"
            stored_val = await engine.store.get_secret(full_key)
            secret_flags[field["secret_name"]] = bool(stored_val and stored_val.strip())

        pairing_active = False
        if name == "telegram":
            adapter = registry.get("telegram")
            pairing_active = bool(getattr(adapter, "pairing_active", False))
            session_val = await engine.store.get_secret(TELEGRAM_APP_SESSION_SECRET_KEY)
            secret_flags["app_session"] = bool(session_val and session_val.strip())
            for field in TELEGRAM_APP_GUIDE["fields"]:
                full_key = f"telegram.{field['secret_name']}"
                stored_val = await engine.store.get_secret(full_key)
                secret_flags[field["secret_name"]] = bool(
                    stored_val and stored_val.strip()
                )

        row: dict[str, Any] = {
            "guide": guide,
            "enabled": bool(st.get("enabled")),
            "running": bool(st.get("running")),
            "has_secret": bool(st.get("has_secret")),
            "secret_flags": secret_flags,
            "pairing_active": pairing_active,
        }
        if name == "telegram":
            login_step = telegram_app.login_step if telegram_app is not None else "idle"
            row["app_guide"] = TELEGRAM_APP_GUIDE
            row["app_step"] = _telegram_app_step(secret_flags, login_step=login_step)
            row["app_phone_masked"] = (
                telegram_app.pending_phone_masked if telegram_app is not None else None
            )
        rows.append(row)
    return rows


async def _harness_secret_status(engine: Engine) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for field in HARNESS_SECRET_FIELDS:
        key = str(field["key"])
        exists = (await engine.store.get_secret(key)) is not None
        out.append({**field, "exists": exists})
    return out


def mount_ui(app: FastAPI) -> None:
    """Attach ``/ui`` shell, settings forms, and static assets (idempotent)."""
    if getattr(app.state, "ui_mounted", False):
        return

    app.mount(
        "/ui/static",
        StaticFiles(directory=str(_UI_ROOT / "static")),
        name="ui-static",
    )

    @app.get(
        "/ui",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_dashboard(
        request: Request,
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        hooks = await eng.store.list_hooks()
        events = await eng.store.list_events(limit=20, offset=0)
        executions = await eng.store.list_executions(limit=20, offset=0)
        ctx: dict[str, Any] = {}
        ctx.update(
            {
                "hooks": hooks,
                "events": events,
                "executions": executions,
                "page": "overview",
                "agent_brief": render_agent_brief(str(request.base_url)),
            }
        )
        return _TEMPLATES.TemplateResponse(request, "dashboard.html", ctx)

    @app.get(
        "/ui/agent-brief.txt",
        response_class=PlainTextResponse,
        include_in_schema=False,
    )
    async def ui_agent_brief_text(request: Request) -> PlainTextResponse:
        return PlainTextResponse(
            render_agent_brief(str(request.base_url)),
            media_type="text/plain; charset=utf-8",
        )

    @app.get(
        "/ui/settings",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_settings(
        request: Request,
        saved: str | None = Query(default=None),
        err: str | None = Query(default=None),
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        reg: ChannelRegistry = request.app.state.channel_registry
        tg_app: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        ctx: dict[str, Any] = {}
        ctx.update(
            {
                "page": "settings",
                "channels": await _channel_rows(
                    reg, engine=eng, telegram_app=tg_app
                ),
                "jira_info": JIRA_INFO,
                "harness_secrets": await _harness_secret_status(eng),
                "flash_saved": saved,
                "flash_err": err,
            }
        )
        return _TEMPLATES.TemplateResponse(request, "settings.html", ctx)

    @app.post(
        "/ui/settings/channels/{name}/secret",
        include_in_schema=False,
    )
    async def ui_set_channel_secret(
        name: str,
        request: Request,
        value: str = Form(default=""),
        secret_name: str = Form(...),
    ) -> RedirectResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        try:
            await reg.set_channel_secret(name, value, secret_name=secret_name)
        except ValueError as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(str(exc))}",
                status_code=303,
            )
        service: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        if service is not None and name == "telegram" and secret_name in _APP_CREDENTIAL_NAMES:
            service.cancel_login()
        return RedirectResponse(
            f"/ui/settings?saved={quote(f'{name}.{secret_name}')}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/telegram/pair-start",
        include_in_schema=False,
    )
    async def ui_telegram_pair_start(request: Request) -> RedirectResponse:
        from pboy.channels.telegram import TelegramChannel

        reg: ChannelRegistry = request.app.state.channel_registry
        adapter = reg.get("telegram")
        if not isinstance(adapter, TelegramChannel) or not adapter.started:
            return RedirectResponse(
                f"/ui/settings?err={quote('먼저 봇 토큰을 저장하고 Telegram 채널을 켜 주세요.')}",
                status_code=303,
            )
        info = await adapter.start_pairing()
        msg = (
            "telegram-pair-sent (대화방으로 코드를 보냈습니다)"
            if info.get("sent_immediately")
            else "telegram-pair-waiting (5분 안에 텔레그램 봇에게 아무 메시지나 보내면 코드가 답장됩니다)"
        )
        return RedirectResponse(
            f"/ui/settings?saved={quote(msg)}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/telegram/pair-verify",
        include_in_schema=False,
    )
    async def ui_telegram_pair_verify(
        request: Request,
        code: str = Form(...),
    ) -> RedirectResponse:
        from pboy.channels.telegram import TelegramChannel

        reg: ChannelRegistry = request.app.state.channel_registry
        adapter = reg.get("telegram")
        if not isinstance(adapter, TelegramChannel):
            return RedirectResponse(
                f"/ui/settings?err={quote('Telegram 채널이 실행 중이 아닙니다.')}",
                status_code=303,
            )
        try:
            await adapter.verify_pairing_code(code)
        except ValueError as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(str(exc))}",
                status_code=303,
            )
        return RedirectResponse(
            f"/ui/settings?saved={quote('telegram.allowlist (인증 코드로 대화방 등록 완료)')}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/telegram/app-login-start",
        include_in_schema=False,
    )
    async def ui_telegram_app_login_start(
        request: Request,
        phone: str = Form(...),
    ) -> RedirectResponse:
        service: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        if service is None:
            return RedirectResponse(
                f"/ui/settings?err={quote('Telegram App 서비스를 사용할 수 없습니다.')}",
                status_code=303,
            )
        try:
            await service.request_login_code(phone)
        except (ValueError, RuntimeError) as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(str(exc))}",
                status_code=303,
            )
        return RedirectResponse(f"/ui/settings{_APP_ANCHOR}", status_code=303)

    @app.post(
        "/ui/settings/channels/telegram/app-login-verify",
        include_in_schema=False,
    )
    async def ui_telegram_app_login_verify(
        request: Request,
        code: str = Form(...),
        password: str = Form(default=""),
    ) -> RedirectResponse:
        service: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        if service is None:
            return RedirectResponse(
                f"/ui/settings?err={quote('Telegram App 서비스를 사용할 수 없습니다.')}",
                status_code=303,
            )
        try:
            info = await service.verify_login_code(
                code,
                password=password.strip() or None,
            )
        except (ValueError, RuntimeError) as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(str(exc))}",
                status_code=303,
            )
        if info.get("password_required"):
            return RedirectResponse(f"/ui/settings{_APP_ANCHOR}", status_code=303)
        return _app_connected_redirect(info)

    @app.post(
        "/ui/settings/channels/telegram/app-login-password",
        include_in_schema=False,
    )
    async def ui_telegram_app_login_password(
        request: Request,
        password: str = Form(...),
    ) -> RedirectResponse:
        service: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        if service is None:
            return RedirectResponse(
                f"/ui/settings?err={quote('Telegram App 서비스를 사용할 수 없습니다.')}",
                status_code=303,
            )
        try:
            info = await service.verify_login_password(password)
        except (ValueError, RuntimeError) as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(str(exc))}",
                status_code=303,
            )
        return _app_connected_redirect(info)

    @app.post(
        "/ui/settings/channels/telegram/app-login-cancel",
        include_in_schema=False,
    )
    async def ui_telegram_app_login_cancel(request: Request) -> RedirectResponse:
        service: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        if service is not None:
            service.cancel_login()
        return RedirectResponse(f"/ui/settings{_APP_ANCHOR}", status_code=303)

    @app.post(
        "/ui/settings/channels/telegram/app-disconnect",
        include_in_schema=False,
    )
    async def ui_telegram_app_disconnect(request: Request) -> RedirectResponse:
        service: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        if service is None:
            return RedirectResponse(
                f"/ui/settings?err={quote('Telegram App 서비스를 사용할 수 없습니다.')}",
                status_code=303,
            )
        await service.disconnect_account()
        return RedirectResponse(
            f"/ui/settings?saved={quote('telegram.app_session (계정 연결 해제)')}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/{name}/enable",
        include_in_schema=False,
    )
    async def ui_enable_channel(name: str, request: Request) -> RedirectResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        try:
            await reg.enable(name)
        except Exception as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(str(exc))}",
                status_code=303,
            )
        return RedirectResponse(
            f"/ui/settings?saved={quote(f'{name}-enabled')}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/{name}/disable",
        include_in_schema=False,
    )
    async def ui_disable_channel(name: str, request: Request) -> RedirectResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        await reg.disable(name)
        return RedirectResponse(
            f"/ui/settings?saved={quote(f'{name}-disabled')}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/harness-secret",
        include_in_schema=False,
    )
    async def ui_set_harness_secret(
        request: Request,
        key: str = Form(...),
        value: str = Form(...),
    ) -> RedirectResponse:
        if key not in _HARNESS_KEYS:
            return RedirectResponse(
                f"/ui/settings?err={quote('Unknown harness secret key')}",
                status_code=303,
            )
        if not value.strip():
            return RedirectResponse(
                f"/ui/settings?err={quote('Value must not be empty')}",
                status_code=303,
            )
        eng: Engine = request.app.state.engine
        await eng.store.put_secret(key, value.strip())
        return RedirectResponse(
            f"/ui/settings?saved={quote(key)}",
            status_code=303,
        )

    @app.get(
        "/ui/partials/hooks",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_partial_hooks(request: Request) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        hooks = await eng.store.list_hooks()
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/hooks.html",
            {"hooks": hooks},
        )

    @app.get(
        "/ui/partials/events",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_partial_events(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        events = await eng.store.list_events(limit=limit, offset=0)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/events.html",
            {"events": events},
        )

    @app.get(
        "/ui/partials/executions",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_partial_executions(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        executions = await eng.store.list_executions(limit=limit, offset=0)
        return _TEMPLATES.TemplateResponse(
            request,
            "partials/executions.html",
            {"executions": executions},
        )

    app.state.ui_mounted = True
