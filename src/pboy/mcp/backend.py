"""Backend for MCP tools — daemon HTTP when available, else local SQLite."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import httpx

from pboy.cli.daemon_client import DaemonClient, resolve_daemon_url
from pboy.models.hook import HookCreate
from pboy.store.sqlite import SQLiteStore

T = TypeVar("T")


class McpBackendError(Exception):
    """User-facing MCP tool failure (missing event, bad args, daemon HTTP, …)."""


class McpBackend:
    """Thin facade over ``DaemonClient`` / ``SQLiteStore`` for MCP tools."""

    def __init__(self, daemon_url: str | None = None) -> None:
        # Explicit URL wins; otherwise auto-probe like the CLI.
        self._daemon_url = resolve_daemon_url(daemon_url)

    @property
    def daemon_url(self) -> str | None:
        return self._daemon_url

    def _client(self) -> DaemonClient | None:
        if not self._daemon_url:
            return None
        return DaemonClient(self._daemon_url)

    def _run_store(self, coro_fn: Callable[[SQLiteStore], Coroutine[Any, Any, T]]) -> T:
        async def _runner() -> T:
            store = SQLiteStore()
            await store.init_db()
            try:
                return await coro_fn(store)
            finally:
                await store.close()

        return asyncio.run(_runner())

    def status(self) -> dict[str, Any]:
        client = self._client()
        if client is not None:
            try:
                body = client.healthz()
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc
            return {"mode": "daemon", "daemon_url": self._daemon_url, **body}

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            from pboy.channels.registry import ChannelRegistry
            from pboy.core.config import get_settings

            async def _noop_emit(*_a: Any, **_k: Any) -> None:
                return None

            reg = ChannelRegistry(emit=_noop_emit, store=store, settings=get_settings())
            return {
                "mode": "local_sqlite",
                "daemon_url": None,
                "db_path": str(store.db_path),
                "channels": await reg.status(),
            }

        return self._run_store(_run)

    def list_events(self, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        client = self._client()
        if client is not None:
            try:
                events = client.list_events(limit=limit, offset=offset)
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc
            return {
                "events": [
                    {"id": e.id, "source": e.source, "type": e.type, "time": e.time}
                    for e in events
                ]
            }

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            rows = await store.list_events(limit=limit, offset=offset)
            return {
                "events": [
                    {"id": e.id, "source": e.source, "type": e.type, "time": e.time}
                    for e in rows
                ]
            }

        return self._run_store(_run)

    def get_event(self, event_id: str) -> dict[str, Any]:
        eid = event_id.strip()
        if not eid:
            raise McpBackendError("event_id is required")
        client = self._client()
        if client is not None:
            try:
                detail = client.get_event_detail(eid)
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc
            if detail is None:
                raise McpBackendError(f"Event '{eid}' not found")
            return detail

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            from pboy.core.engine import Engine

            detail = await Engine(store=store).event_detail(eid)
            if detail is None:
                raise McpBackendError(f"Event '{eid}' not found")
            return detail

        return self._run_store(_run)

    def reply_event(
        self,
        event_id: str,
        *,
        summary: str = "Completed",
        sync: str | None = None,
    ) -> dict[str, Any]:
        """Reply on the originating chat (Telegram bot or Slack thread).

        Default ``sync`` is derived from the event ``source``. This is the
        bot-token outbound path — not Telegram App API.
        """
        eid = event_id.strip()
        if not eid:
            raise McpBackendError("event_id is required")

        target = (sync or "").strip().lower() or None
        if target is None:
            detail = self.get_event(eid)
            source = str(detail.get("source") or "")
            try:
                target = _reply_sync_for_event_source(source)
            except ValueError as exc:
                raise McpBackendError(str(exc)) from exc
        if target not in ("telegram", "slack"):
            raise McpBackendError(
                f"sync must be 'telegram' or 'slack', got {target!r}"
            )

        client = self._client()
        if client is not None:
            try:
                return client.complete_event(eid, summary=summary, sync=[target])
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            from pboy.core.engine import Engine

            engine = Engine(store=store)
            try:
                completion, already = await engine.complete_event(
                    eid, summary=summary, sync=[target]
                )
            except KeyError as exc:
                raise McpBackendError(str(exc.args[0] if exc.args else exc)) from exc
            except ValueError as exc:
                raise McpBackendError(str(exc)) from exc
            return {
                "event_id": eid,
                "status": completion.status,
                "summary": completion.summary,
                "completed_at": completion.completed_at,
                "already_completed": already,
                "sync_results": [r.model_dump() for r in completion.sync_results],
            }

        return self._run_store(_run)

    def list_hooks(self) -> dict[str, Any]:
        client = self._client()
        if client is not None:
            try:
                hooks = client.list_hooks()
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc
            return {"hooks": [h.model_dump() for h in hooks]}

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            hooks = await store.list_hooks()
            return {"hooks": [h.model_dump() for h in hooks]}

        return self._run_store(_run)

    def register_hook(
        self,
        *,
        name: str,
        topic: str,
        command: str,
        filter: str | None = None,
        timeout: int = 180,
        concurrency: int = 1,
    ) -> dict[str, Any]:
        try:
            payload = HookCreate(
                name=name,
                topic=topic,
                exec=command,
                filter=filter,
                timeout=timeout,
                concurrency=concurrency,
            )
        except Exception as exc:
            raise McpBackendError(str(exc)) from exc

        client = self._client()
        if client is not None:
            try:
                hook = client.create_hook(payload)
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc
            return hook.model_dump()

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            hook = await store.create_hook(payload)
            return hook.model_dump()

        return self._run_store(_run)

    def delete_hook(self, name: str) -> dict[str, Any]:
        cleaned = name.strip()
        if not cleaned:
            raise McpBackendError("hook name is required")
        client = self._client()
        if client is not None:
            try:
                deleted = client.delete_hook(cleaned)
            except httpx.HTTPError as exc:
                raise McpBackendError(f"Daemon request failed: {exc}") from exc
            if not deleted:
                raise McpBackendError(f"Hook '{cleaned}' not found")
            return {"deleted": True, "name": cleaned}

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            deleted = await store.delete_hook(cleaned)
            if not deleted:
                raise McpBackendError(f"Hook '{cleaned}' not found")
            return {"deleted": True, "name": cleaned}

        return self._run_store(_run)

    def telegram_list_chats(
        self,
        *,
        limit: int = 50,
        search: str | None = None,
    ) -> dict[str, Any]:
        """List dialogs via Telegram App API (user account — not bot replies)."""

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            from pboy.channels.telegram_app import TelegramAppService
            from pboy.core.config import get_settings

            service = TelegramAppService(store=store, settings=get_settings())
            try:
                rows = await service.list_chats(limit=limit, search=search)
            except RuntimeError as exc:
                raise McpBackendError(str(exc)) from exc
            return {"chats": rows}

        return self._run_store(_run)

    def telegram_list_messages(
        self,
        chat: str,
        *,
        limit: int = 50,
        offset_id: int = 0,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Fetch messages via Telegram App API (read-only; not for bot replies)."""

        async def _run(store: SQLiteStore) -> dict[str, Any]:
            from pboy.channels.telegram_app import TelegramAppService
            from pboy.core.config import get_settings

            service = TelegramAppService(store=store, settings=get_settings())
            try:
                body = await service.list_messages(
                    chat,
                    limit=limit,
                    offset_id=offset_id,
                    search=search,
                )
            except (RuntimeError, ValueError, LookupError) as exc:
                raise McpBackendError(str(exc)) from exc
            return body

        return self._run_store(_run)


def _reply_sync_for_event_source(source: str) -> str:
    name = source.strip().lower()
    if name == "telegram":
        return "telegram"
    if name == "slack":
        return "slack"
    raise ValueError(
        f"reply_event has no outbound for source={source!r} "
        "(supported: telegram, slack). Pass sync explicitly if needed."
    )
