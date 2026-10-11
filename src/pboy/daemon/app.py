"""FastAPI application for the Pageboy daemon."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

from pboy.core.engine import Engine
from pboy.daemon.schemas import (
    EventInjectRequest,
    EventInjectResponse,
    ExecutionSummary,
)
from pboy.store.sqlite import SQLiteStore


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine: Engine = app.state.engine
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()


def create_app(
    *,
    db_path: str | Path | None = None,
    secret_key: str | None = None,
) -> FastAPI:
    """Build daemon app with a dedicated Engine/store lifecycle."""
    store = SQLiteStore(db_path=db_path, secret_key=secret_key)
    engine = Engine(store=store)

    app = FastAPI(
        title="Pageboy Daemon",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.engine = engine
    app.state.db_path = str(store.db_path)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "pboy-daemon",
            "db_path": app.state.db_path,
        }

    @app.post("/v1/events", response_model=EventInjectResponse)
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

    return app
