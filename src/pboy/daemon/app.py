"""FastAPI application for the Pageboy daemon."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from pboy.channels.base import ChannelAdapter
from pboy.channels.registry import ChannelRegistry
from pboy.channels.telegram_app import TelegramAppService
from pboy.core.config import Settings, get_settings
from pboy.core.engine import Engine
from pboy.daemon.guard import LocalOnlyGuard
from pboy.daemon.schemas import (
    ChannelListResponse,
    ChannelMutationResponse,
    ChannelStatusItem,
    EventCompleteRequest,
    EventCompleteResponse,
    EventInjectRequest,
    EventInjectResponse,
    EventListItem,
    EventListResponse,
    ExecutionSummary,
    HookCreateRequest,
    HookDeleteResponse,
    HookListResponse,
    SecretPutRequest,
    SecretPutResponse,
    SecretStatusResponse,
    StateGetResponse,
    StatePutRequest,
    StatePutResponse,
    SyncResultModel,
)
from pboy.models.hook import Hook
from pboy.models.reply_file import ReplyFile, parse_reply_file_type
from pboy.store.sqlite import SQLiteStore


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine: Engine = app.state.engine
    registry: ChannelRegistry = app.state.channel_registry
    await engine.start()
    await registry.start_enabled()
    try:
        yield
    finally:
        await registry.stop_all()
        await engine.stop()


def create_app(
    *,
    db_path: str | Path | None = None,
    secret_key: str | None = None,
    settings: Settings | None = None,
    extra_adapters: list[ChannelAdapter] | None = None,
    enable_ui: bool = False,
) -> FastAPI:
    """Build daemon app with a dedicated Engine/store/channel lifecycle."""
    cfg = settings or get_settings()
    store = SQLiteStore(db_path=db_path, secret_key=secret_key)
    engine = Engine(store=store, settings=cfg)
    registry = ChannelRegistry(emit=engine.process_event, store=store, settings=cfg)
    for adapter in extra_adapters or []:
        registry.register(adapter)
    telegram_app = TelegramAppService(store=store, settings=cfg)

    app = FastAPI(
        title="Pageboy Daemon",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.add_middleware(LocalOnlyGuard)
    app.state.engine = engine
    app.state.db_path = str(store.db_path)
    app.state.channel_registry = registry
    app.state.telegram_app = telegram_app
    app.state.ui_enabled = enable_ui

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        reg: ChannelRegistry = request.app.state.channel_registry
        return {
            "status": "ok",
            "service": "pboy-daemon",
            "db_path": app.state.db_path,
            "channels": await reg.status(),
        }

    @app.post(
        "/v1/events",
        response_model=EventInjectResponse,
    )
    async def inject_event(body: EventInjectRequest, request: Request) -> EventInjectResponse:
        eng: Engine = request.app.state.engine
        event, matched, executions = await eng.process_event(
            body.payload,
            body.source,
            dispatch=body.dispatch,
        )
        return EventInjectResponse(
            event_id=event.id,
            source=event.source,
            type=event.type,
            matched_hooks=[h.name for h in matched],
            executions=[
                ExecutionSummary(
                    execution_id=ex.execution_id,
                    hook_name=ex.hook_name,
                    status=ex.status,
                    exit_code=ex.exit_code,
                    stdout=ex.stdout,
                    stderr=ex.stderr,
                )
                for ex in executions
            ],
        )

    @app.get("/v1/events", response_model=EventListResponse)
    async def list_events(
        request: Request,
        limit: int = Query(default=20, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> EventListResponse:
        eng: Engine = request.app.state.engine
        events = await eng.store.list_events(limit=limit, offset=offset)
        return EventListResponse(
            events=[
                EventListItem(id=e.id, source=e.source, type=e.type, time=e.time) for e in events
            ]
        )

    @app.get("/v1/events/{event_id}")
    async def get_event(event_id: str, request: Request) -> dict[str, Any]:
        eng: Engine = request.app.state.engine
        detail = await eng.event_detail(event_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
        return detail

    @app.post(
        "/v1/events/{event_id}/complete",
        response_model=EventCompleteResponse,
    )
    async def complete_event(
        event_id: str, body: EventCompleteRequest, request: Request
    ) -> EventCompleteResponse:
        eng: Engine = request.app.state.engine
        reply_file: ReplyFile | None = None
        if body.file_path or body.file_id:
            try:
                reply_file = ReplyFile(
                    file_path=body.file_path,
                    file_id=body.file_id,
                    file_type=parse_reply_file_type(body.file_type),
                )
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            completion, already = await eng.complete_event(
                event_id,
                summary=body.summary,
                sync=body.sync,
                reply_file=reply_file,
            )
        except KeyError as exc:
            detail = exc.args[0] if exc.args else f"Event '{event_id}' not found"
            raise HTTPException(status_code=404, detail=detail) from exc

        return EventCompleteResponse(
            event_id=event_id,
            status=completion.status,
            summary=completion.summary,
            completed_at=completion.completed_at,
            already_completed=already,
            sync_results=[
                SyncResultModel.model_validate(r.model_dump()) for r in completion.sync_results
            ],
        )

    @app.put(
        "/v1/events/{event_id}/state",
        response_model=StatePutResponse,
    )
    async def put_state(
        event_id: str, body: StatePutRequest, request: Request
    ) -> StatePutResponse:
        eng: Engine = request.app.state.engine
        await eng.store.put_state(event_id, body.key, body.value)
        return StatePutResponse(event_id=event_id, key=body.key, value=body.value)

    @app.get("/v1/events/{event_id}/state", response_model=StateGetResponse)
    async def get_state(
        event_id: str,
        request: Request,
        key: str = Query(default="execution"),
    ) -> StateGetResponse:
        eng: Engine = request.app.state.engine
        value = await eng.store.get_state(event_id, key)
        return StateGetResponse(event_id=event_id, key=key, value=value)

    @app.get("/v1/hooks", response_model=HookListResponse)
    async def list_hooks(request: Request) -> HookListResponse:
        eng: Engine = request.app.state.engine
        hooks = await eng.store.list_hooks()
        return HookListResponse(hooks=hooks)

    @app.post(
        "/v1/hooks",
        response_model=Hook,
    )
    async def create_hook(body: HookCreateRequest, request: Request) -> Hook:
        eng: Engine = request.app.state.engine
        return await eng.store.create_hook(body)

    @app.delete(
        "/v1/hooks/{name}",
        response_model=HookDeleteResponse,
    )
    async def delete_hook(name: str, request: Request) -> HookDeleteResponse:
        eng: Engine = request.app.state.engine
        deleted = await eng.store.delete_hook(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")
        return HookDeleteResponse(deleted=True, name=name)

    @app.get("/v1/channels", response_model=ChannelListResponse)
    async def list_channels(request: Request) -> ChannelListResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        status = await reg.status()
        rows = await reg.list_status()
        return ChannelListResponse(
            channels=[ChannelStatusItem.model_validate(row) for row in rows],
            env_override=bool(status.get("env_override")),
        )

    @app.post(
        "/v1/channels/{name}/enable",
        response_model=ChannelMutationResponse,
    )
    async def enable_channel(name: str, request: Request) -> ChannelMutationResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        try:
            await reg.enable(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        enabled = name.strip().lower() in await reg.resolve_enabled_names()
        return ChannelMutationResponse(
            name=name.strip().lower(),
            enabled=enabled,
            running=name.strip().lower() in reg.running,
        )

    @app.post(
        "/v1/channels/{name}/disable",
        response_model=ChannelMutationResponse,
    )
    async def disable_channel(name: str, request: Request) -> ChannelMutationResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        await reg.disable(name)
        key = name.strip().lower()
        return ChannelMutationResponse(
            name=key,
            enabled=key in await reg.resolve_enabled_names(),
            running=key in reg.running,
        )

    @app.put(
        "/v1/secrets/{key:path}",
        response_model=SecretPutResponse,
    )
    async def put_secret(key: str, body: SecretPutRequest, request: Request) -> SecretPutResponse:
        if not body.value.strip():
            raise HTTPException(status_code=422, detail="Secret value must not be empty")
        eng: Engine = request.app.state.engine
        await eng.store.put_secret(key, body.value)
        return SecretPutResponse(key=key, stored=True)

    @app.get("/v1/secrets/{key:path}/status", response_model=SecretStatusResponse)
    async def secret_status(key: str, request: Request) -> SecretStatusResponse:
        eng: Engine = request.app.state.engine
        exists = (await eng.store.get_secret(key)) is not None
        return SecretStatusResponse(key=key, exists=exists)

    @app.put(
        "/v1/channels/{name}/secret",
        response_model=SecretPutResponse,
    )
    async def put_channel_secret(
        name: str, body: SecretPutRequest, request: Request
    ) -> SecretPutResponse:
        reg: ChannelRegistry = request.app.state.channel_registry
        try:
            secret_key = await reg.set_channel_secret(
                name, body.value, secret_name=body.secret_name
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SecretPutResponse(key=secret_key, stored=True)

    if enable_ui:
        from pboy.ui.mount import mount_ui

        mount_ui(app)

    return app
