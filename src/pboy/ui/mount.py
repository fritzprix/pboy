"""Mount HTMX dashboard + settings forms on the daemon FastAPI app."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context
from markupsafe import Markup

from pboy.channels.registry import ChannelRegistry
from pboy.channels.telegram_app import (
    TELEGRAM_APP_SESSION_SECRET_KEY,
    TelegramAppService,
)
from pboy.core.engine import Engine
from pboy.i18n import (
    HTML_LANG,
    LANGUAGE_NAMES,
    SUPPORTED_LANGS,
    Translator,
    localize_error,
    negotiate_lang,
    normalize_lang,
)
from pboy.ui.agent_brief import render_agent_brief
from pboy.ui.formatting import duration, short_id, when
from pboy.ui.guides import (
    HARNESS_SECRET_KEYS,
    channel_guides,
    harness_secret_fields,
    jira_info,
    telegram_app_guide,
)

_UI_ROOT = Path(__file__).resolve().parent
LANG_COOKIE = "pboy_lang"


def request_lang(request: Request) -> str:
    """Viewer language: ``pboy_lang`` cookie, then ``Accept-Language``, then English."""
    return normalize_lang(request.cookies.get(LANG_COOKIE)) or negotiate_lang(
        request.headers.get("accept-language")
    )


def _i18n_context(request: Request) -> dict[str, Any]:
    t = Translator(request_lang(request))
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    return {
        "t": t,
        "th": t.html,
        "lang": t.lang,
        "html_lang": HTML_LANG.get(t.lang, t.lang),
        "languages": [(code, LANGUAGE_NAMES[code]) for code in SUPPORTED_LANGS],
        "current_path": path,
    }


@pass_context
def _when_filter(ctx: Context, value: str | None) -> Markup:
    return when(value, lang=ctx.get("lang") or "en")


_TEMPLATES = Jinja2Templates(
    directory=str(_UI_ROOT / "templates"),
    context_processors=[_i18n_context],
)
_TEMPLATES.env.filters["when"] = _when_filter
_TEMPLATES.env.filters["short_id"] = short_id
_TEMPLATES.env.globals["duration"] = duration

_HARNESS_KEYS = frozenset(HARNESS_SECRET_KEYS)


_APP_CREDENTIAL_NAMES = frozenset({"api_id", "api_hash"})
_BOT_SECRET_NAMES = frozenset({"bot_token", "allowlist"})
_APP_ANCHOR = "#telegram-app-api"
_BOT_ANCHOR = "#telegram-bot"


def _tr(request: Request) -> Translator:
    return Translator(request_lang(request))


def _err_text(exc: Exception, request: Request) -> str:
    """Exception text for the ``err`` flash, localized when the error supports it."""
    return localize_error(exc, request_lang(request))


def _flash(kind: str, text: str, anchor: str = "") -> RedirectResponse:
    """Redirect to settings with an ``ok`` / ``err`` / ``saved`` flash."""
    return RedirectResponse(f"/ui/settings?{kind}={quote(text)}{anchor}", status_code=303)


def _app_connected_redirect(request: Request, info: dict[str, Any]) -> RedirectResponse:
    who = info.get("username") or info.get("user_id") or "ok"
    return _flash("ok", _tr(request)("flash.app_connected", who=who), _APP_ANCHOR)


def _telegram_app_step(secret_flags: dict[str, bool], *, login_step: str) -> str:
    """App API wizard step: credentials → phone → code (→ password) → connected."""
    if not secret_flags.get("api_id") or not secret_flags.get("api_hash"):
        return "credentials"
    if secret_flags.get("app_session"):
        return "connected"
    if login_step in ("code", "password"):
        return login_step
    return "phone"


def _telegram_bot_step(
    secret_flags: dict[str, bool],
    *,
    enabled: bool,
    receiving_ok: bool,
) -> str:
    """Bot wake-up wizard: token → enable → pair → protected.

    Stays on ``enable`` when the channel is off, not yet receiving, or the bot
    token was rejected (``receiving_ok`` is False) so pairing is not offered.
    """
    if not secret_flags.get("bot_token"):
        return "token"
    if not enabled or not receiving_ok:
        return "enable"
    if not secret_flags.get("allowlist"):
        return "pair"
    return "protected"


def _telegram_settings_anchor(secret_name: str) -> str:
    if secret_name in _APP_CREDENTIAL_NAMES:
        return _APP_ANCHOR
    if secret_name in _BOT_SECRET_NAMES:
        return _BOT_ANCHOR
    return "#channel-telegram"


async def _channel_rows(
    registry: ChannelRegistry,
    *,
    engine: Engine,
    t: Translator,
    telegram_app: TelegramAppService | None = None,
) -> list[dict[str, Any]]:
    status_rows = await registry.list_status()
    by_name = {str(r["name"]): r for r in status_rows}
    app_guide = telegram_app_guide(t)
    rows: list[dict[str, Any]] = []
    for guide in channel_guides(t):
        name = guide["name"]
        st = by_name.get(name, {})
        secret_flags: dict[str, bool] = {}
        for field in guide["fields"]:
            full_key = f"{name}.{field['secret_name']}"
            stored_val = await engine.store.get_secret(full_key)
            secret_flags[field["secret_name"]] = bool(stored_val and stored_val.strip())

        pairing_active = False
        receiving_ok = bool(st.get("running"))
        bot_last_error: str | None = None
        if name == "telegram":
            adapter = registry.get("telegram")
            pairing_active = bool(getattr(adapter, "pairing_active", False))
            if adapter is not None:
                receiving_ok = bool(getattr(adapter, "receiving_ok", False))
                err_i18n = getattr(adapter, "last_error_i18n", None)
                err = getattr(adapter, "last_error", None)
                if err_i18n:
                    bot_last_error = t(err_i18n[0], **err_i18n[1])
                else:
                    bot_last_error = str(err) if err else None
            else:
                receiving_ok = False
            session_val = await engine.store.get_secret(TELEGRAM_APP_SESSION_SECRET_KEY)
            secret_flags["app_session"] = bool(session_val and session_val.strip())
            for field in app_guide["fields"]:
                full_key = f"telegram.{field['secret_name']}"
                stored_val = await engine.store.get_secret(full_key)
                secret_flags[field["secret_name"]] = bool(
                    stored_val and stored_val.strip()
                )

        row: dict[str, Any] = {
            "guide": guide,
            "enabled": bool(st.get("enabled")),
            "running": bool(st.get("running")),
            "receiving_ok": receiving_ok,
            "has_secret": bool(st.get("has_secret")),
            "secret_flags": secret_flags,
            "pairing_active": pairing_active,
        }
        if name == "telegram":
            login_step = telegram_app.login_step if telegram_app is not None else "idle"
            row["bot_last_error"] = bot_last_error
            row["bot_step"] = _telegram_bot_step(
                secret_flags,
                enabled=bool(st.get("enabled")),
                receiving_ok=receiving_ok,
            )
            row["app_guide"] = app_guide
            row["app_step"] = _telegram_app_step(secret_flags, login_step=login_step)
            row["app_phone_masked"] = (
                telegram_app.pending_phone_masked if telegram_app is not None else None
            )
        rows.append(row)
    return rows


async def _harness_secret_status(engine: Engine, t: Translator) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for field in harness_secret_fields(t):
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

    @app.get("/ui/lang", include_in_schema=False)
    async def ui_set_lang(
        lang: str = Query(default=""),
        next: str = Query(default="/ui"),
    ) -> RedirectResponse:
        """Remember the picked language in a cookie and go back to the page."""
        # Only same-app paths, so the switcher cannot become an open redirect.
        target = next if next.startswith("/ui") and not next.startswith("//") else "/ui"
        response = RedirectResponse(target, status_code=303)
        code = normalize_lang(lang)
        if code:
            response.set_cookie(LANG_COOKIE, code, max_age=365 * 86400, path="/", samesite="lax")
        return response

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
        "/ui/events/{event_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_event_detail(event_id: str, request: Request) -> HTMLResponse:
        """Event payload plus its executions with stdout/stderr.

        Same data as ``GET /v1/events/{id}`` / ``pboy event get --verbose``; the
        daemon only answers loopback callers, so output is shown to the local operator.
        """
        eng: Engine = request.app.state.engine
        detail = await eng.event_detail(event_id)
        if detail is None:
            return _TEMPLATES.TemplateResponse(
                request,
                "event.html",
                {"page": "overview", "event": None, "event_id": event_id},
                status_code=404,
            )
        return _TEMPLATES.TemplateResponse(
            request,
            "event.html",
            {
                "page": "overview",
                "event": detail,
                "executions": detail["executions"],
                "data_json": json.dumps(detail.get("data"), indent=2, ensure_ascii=False),
            },
        )

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
        ok: str | None = Query(default=None),
        err: str | None = Query(default=None),
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        reg: ChannelRegistry = request.app.state.channel_registry
        tg_app: TelegramAppService | None = getattr(
            request.app.state, "telegram_app", None
        )
        t = _tr(request)
        ctx: dict[str, Any] = {}
        ctx.update(
            {
                "page": "settings",
                "channels": await _channel_rows(
                    reg, engine=eng, t=t, telegram_app=tg_app
                ),
                "jira_info": jira_info(t),
                "harness_secrets": await _harness_secret_status(eng, t),
                "flash_saved": saved,
                "flash_ok": ok,
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
                f"/ui/settings?err={quote(_err_text(exc, request))}",
                status_code=303,
            )
        if name == "telegram":
            anchor = _telegram_settings_anchor(secret_name)
        else:
            anchor = f"#channel-{name}"
        return RedirectResponse(
            f"/ui/settings?saved={quote(f'{name}.{secret_name}')}{anchor}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/{name}/secrets",
        include_in_schema=False,
    )
    async def ui_set_channel_secrets(name: str, request: Request) -> RedirectResponse:
        """Save several related secrets from one form (``secret__<name>`` fields).

        Empty inputs keep the stored value, so one field can be updated alone.
        A checked ``clear__<name>`` box clears an optional secret unless a new
        value was typed for it.
        """
        reg: ChannelRegistry = request.app.state.channel_registry
        form = await request.form()
        updates = [
            (key.removeprefix("secret__"), str(raw).strip())
            for key, raw in form.items()
            if key.startswith("secret__") and str(raw).strip()
        ]
        typed = {secret_name for secret_name, _ in updates}
        clears = [
            key.removeprefix("clear__")
            for key in form
            if key.startswith("clear__") and key.removeprefix("clear__") not in typed
        ]
        changes = updates + [(secret_name, "") for secret_name in clears]
        anchor = f"#channel-{name}"
        if name == "telegram" and changes:
            anchor = _telegram_settings_anchor(changes[0][0])
        if not changes:
            return _flash("err", _tr(request)("flash.need_value"), anchor)
        try:
            for secret_name, value in changes:
                await reg.set_channel_secret(name, value, secret_name=secret_name)
        except ValueError as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(_err_text(exc, request))}{anchor}",
                status_code=303,
            )
        saved = ", ".join(f"{name}.{secret_name}" for secret_name, _ in changes)
        return RedirectResponse(
            f"/ui/settings?saved={quote(saved)}{anchor}",
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
            return _flash("err", _tr(request)("flash.need_bot"), _BOT_ANCHOR)
        # The code message goes out in the language of whoever started pairing.
        info = await adapter.start_pairing(lang=request_lang(request))
        key = "flash.pair_sent" if info.get("sent_immediately") else "flash.pair_waiting"
        return _flash("ok", _tr(request)(key), _BOT_ANCHOR)

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
            return _flash("err", _tr(request)("flash.tg_not_running"), _BOT_ANCHOR)
        try:
            await adapter.verify_pairing_code(code)
        except ValueError as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(_err_text(exc, request))}{_BOT_ANCHOR}",
                status_code=303,
            )
        return _flash("ok", _tr(request)("flash.paired"), _BOT_ANCHOR)

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
            return _flash("err", _tr(request)("flash.app_unavailable"))
        try:
            await service.request_login_code(phone)
        except (ValueError, RuntimeError) as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(_err_text(exc, request))}",
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
            return _flash("err", _tr(request)("flash.app_unavailable"))
        try:
            info = await service.verify_login_code(
                code,
                password=password.strip() or None,
            )
        except (ValueError, RuntimeError) as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(_err_text(exc, request))}",
                status_code=303,
            )
        if info.get("password_required"):
            return RedirectResponse(f"/ui/settings{_APP_ANCHOR}", status_code=303)
        return _app_connected_redirect(request, info)

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
            return _flash("err", _tr(request)("flash.app_unavailable"))
        try:
            info = await service.verify_login_password(password)
        except (ValueError, RuntimeError) as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(_err_text(exc, request))}",
                status_code=303,
            )
        return _app_connected_redirect(request, info)

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
            return _flash("err", _tr(request)("flash.app_unavailable"))
        await service.disconnect_account()
        return _flash("ok", _tr(request)("flash.app_disconnected"), _APP_ANCHOR)

    @app.post(
        "/ui/settings/channels/{name}/enable",
        include_in_schema=False,
    )
    async def ui_enable_channel(name: str, request: Request) -> RedirectResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        anchor = _BOT_ANCHOR if name == "telegram" else f"#channel-{name}"
        try:
            await reg.enable(name)
        except Exception as exc:
            return RedirectResponse(
                f"/ui/settings?err={quote(_err_text(exc, request))}{anchor}",
                status_code=303,
            )
        return _flash("ok", _tr(request)("flash.channel_enabled", name=name), anchor)

    @app.post(
        "/ui/settings/channels/{name}/disable",
        include_in_schema=False,
    )
    async def ui_disable_channel(name: str, request: Request) -> RedirectResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        await reg.disable(name)
        anchor = _BOT_ANCHOR if name == "telegram" else f"#channel-{name}"
        return _flash("ok", _tr(request)("flash.channel_disabled", name=name), anchor)

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
            return _flash("err", _tr(request)("flash.unknown_harness_key"))
        if not value.strip():
            return _flash("err", _tr(request)("flash.value_empty"))
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
