"""Mount HTMX dashboard + settings forms on the daemon FastAPI app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pboy.channels.registry import ChannelRegistry
from pboy.core.config import get_settings
from pboy.core.engine import Engine
from pboy.daemon.auth import (
    UI_SESSION_COOKIE,
    mint_ui_session,
    require_ui_access,
    require_ui_mutation,
    tokens_match,
    ui_request_authorized,
)
from pboy.ui.guides import (
    AUTH_GUIDE,
    CHANNEL_GUIDES,
    HARNESS_SECRET_FIELDS,
    JIRA_INFO,
)

_UI_ROOT = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_UI_ROOT / "templates"))
_UI_DEPS = [Depends(require_ui_access)]
_UI_MUT_DEPS = [Depends(require_ui_mutation)]

_HARNESS_KEYS = frozenset(str(f["key"]) for f in HARNESS_SECRET_FIELDS)


def _ui_token_q(token: str) -> str:
    return f"?token={quote(token)}" if token else ""


def _common_ctx(request: Request, *, token: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    expected = settings.auth_token.strip()
    auth_configured = bool(expected)
    has_session = bool(
        auth_configured
        and ui_request_authorized(request, expected=expected, token=token)
    )
    ui_token = (token or "").strip()
    return {
        "ui_token": ui_token,
        "ui_token_q": _ui_token_q(ui_token),
        "auth_configured": auth_configured,
        "has_session": has_session,
        "auth_guide": AUTH_GUIDE,
    }


async def _channel_rows(
    registry: ChannelRegistry,
    *,
    engine: Engine,
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
            in_store = (await engine.store.get_secret(full_key)) is not None
            secret_flags[field["secret_name"]] = in_store or registry.env_covers_secret(
                full_key
            )

        rows.append(
            {
                "guide": guide,
                "enabled": bool(st.get("enabled")),
                "running": bool(st.get("running")),
                "has_secret": bool(st.get("has_secret")),
                "secret_flags": secret_flags,
            }
        )
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

    @app.get("/ui/login", response_class=HTMLResponse, include_in_schema=False)
    async def ui_login_get(
        request: Request,
        error: str | None = Query(default=None),
    ) -> HTMLResponse:
        ctx = _common_ctx(request)
        ctx.update({"error": error, "page": "login"})
        return _TEMPLATES.TemplateResponse(request, "login.html", ctx)

    @app.post("/ui/login", include_in_schema=False)
    async def ui_login_post(
        request: Request,
        password: str = Form(...),
    ) -> RedirectResponse:
        settings = get_settings()
        expected = settings.auth_token.strip()
        if not expected:
            return RedirectResponse("/ui/settings", status_code=303)
        if not tokens_match(password.strip(), expected):
            return RedirectResponse("/ui/login?error=1", status_code=303)
        resp = RedirectResponse("/ui/settings", status_code=303)
        resp.set_cookie(
            key=UI_SESSION_COOKIE,
            value=mint_ui_session(expected),
            httponly=True,
            samesite="lax",
            path="/",
            max_age=60 * 60 * 24 * 14,
        )
        return resp

    @app.post("/ui/logout", include_in_schema=False)
    async def ui_logout() -> RedirectResponse:
        resp = RedirectResponse("/ui", status_code=303)
        resp.delete_cookie(UI_SESSION_COOKIE, path="/")
        return resp

    @app.get(
        "/ui",
        response_class=HTMLResponse,
        include_in_schema=False,
        dependencies=_UI_DEPS,
    )
    async def ui_dashboard(
        request: Request,
        token: str | None = Query(default=None),
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        hooks = await eng.store.list_hooks()
        events = await eng.store.list_events(limit=20, offset=0)
        executions = await eng.store.list_executions(limit=20, offset=0)
        ctx = _common_ctx(request, token=token)
        ctx.update(
            {
                "hooks": hooks,
                "events": events,
                "executions": executions,
                "page": "overview",
            }
        )
        return _TEMPLATES.TemplateResponse(request, "dashboard.html", ctx)

    @app.get(
        "/ui/settings",
        response_class=HTMLResponse,
        include_in_schema=False,
        dependencies=_UI_DEPS,
    )
    async def ui_settings(
        request: Request,
        token: str | None = Query(default=None),
        saved: str | None = Query(default=None),
        err: str | None = Query(default=None),
    ) -> HTMLResponse:
        eng: Engine = request.app.state.engine
        reg: ChannelRegistry = request.app.state.channel_registry
        ctx = _common_ctx(request, token=token)
        ctx.update(
            {
                "page": "settings",
                "channels": await _channel_rows(reg, engine=eng),
                "jira_info": JIRA_INFO,
                "harness_secrets": await _harness_secret_status(eng),
                "env_override": "PB_CHANNELS_ENABLED" in os.environ,
                "flash_saved": saved,
                "flash_err": err,
            }
        )
        return _TEMPLATES.TemplateResponse(request, "settings.html", ctx)

    @app.post(
        "/ui/settings/channels/{name}/secret",
        include_in_schema=False,
        dependencies=_UI_MUT_DEPS,
    )
    async def ui_set_channel_secret(
        name: str,
        request: Request,
        value: str = Form(...),
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
        return RedirectResponse(
            f"/ui/settings?saved={quote(f'{name}.{secret_name}')}",
            status_code=303,
        )

    @app.post(
        "/ui/settings/channels/{name}/enable",
        include_in_schema=False,
        dependencies=_UI_MUT_DEPS,
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
        dependencies=_UI_MUT_DEPS,
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
        dependencies=_UI_MUT_DEPS,
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
        dependencies=_UI_DEPS,
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
        dependencies=_UI_DEPS,
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
        dependencies=_UI_DEPS,
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
