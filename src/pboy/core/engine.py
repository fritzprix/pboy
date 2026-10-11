"""Core orchestrator engine: integrates Normalizer, Router, Lease, and Store."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from pboy.core.config import Settings, get_settings
from pboy.core.lease import LeaseEngine
from pboy.core.normalizer import EventNormalizer
from pboy.core.router import HookRouter
from pboy.harness.executor import HarnessExecutor
from pboy.models.cloudevent import CloudEvent, current_iso_time
from pboy.models.completion import (
    COMPLETION_STATE_KEY,
    EXECUTION_STATE_KEY,
    EventCompletion,
    SyncResult,
)
from pboy.models.execution import ExecutionData
from pboy.models.hook import Hook
from pboy.outbound.telegram import TelegramOutbound
from pboy.store.base import StoreBase
from pboy.store.sqlite import SQLiteStore


class Engine:
    """Central orchestrator for Pageboy."""

    def __init__(
        self,
        store: StoreBase | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.store = store or SQLiteStore()
        self.settings = settings or get_settings()
        self.normalizer = EventNormalizer()
        self.router = HookRouter()
        self.lease = LeaseEngine()
        self.executor = HarnessExecutor(self.store)
        self._complete_locks: dict[str, asyncio.Lock] = {}
        self._complete_locks_guard = asyncio.Lock()

    async def start(self) -> None:
        """Initialize engine resources and database."""
        await self.store.init_db()

    async def stop(self) -> None:
        """Release engine resources."""
        await self.store.close()

    async def _completion_lock(self, event_id: str) -> asyncio.Lock:
        async with self._complete_locks_guard:
            lock = self._complete_locks.get(event_id)
            if lock is None:
                lock = asyncio.Lock()
                self._complete_locks[event_id] = lock
            return lock

    async def process_event(
        self,
        raw_payload: dict[str, Any],
        source: str,
        *,
        dispatch: bool = True,
    ) -> tuple[CloudEvent, list[Hook], list[ExecutionData]]:
        """Normalize raw payload, persist event, match hooks, and optionally dispatch."""
        event = self.normalizer.normalize(raw_payload, source)
        await self.store.create_event(event)

        hooks = await self.store.list_hooks()
        matched = self.router.match(event, hooks)

        executions: list[ExecutionData] = []
        if dispatch:
            executions = await self.dispatch(event, matched)
        return event, matched, executions

    async def dispatch(
        self,
        event: CloudEvent,
        hooks: list[Hook],
        *,
        concurrent: bool = True,
    ) -> list[ExecutionData]:
        """Acquire leases and run harnesses for matched hooks.

        When concurrent=True, runs independent hooks concurrently via asyncio.gather
        while strictly preserving lease concurrency semantics and result order.
        """
        if not hooks:
            return []

        async def _run_hook(hook: Hook) -> ExecutionData | None:
            token = await self.lease.acquire(hook.name, max_concurrency=hook.concurrency)
            if token is None:
                logger.warning(
                    "No lease available for hook={} event={} concurrency={}",
                    hook.name,
                    event.id,
                    hook.concurrency,
                )
                return None
            try:
                return await self.executor.run(hook, event, lease_token=token)
            finally:
                await self.lease.release(token)

        if concurrent:
            results = await asyncio.gather(*(_run_hook(h) for h in hooks))
            return [res for res in results if res is not None]

        executions: list[ExecutionData] = []
        for hook in hooks:
            res = await _run_hook(hook)
            if res is not None:
                executions.append(res)
        return executions

    async def complete_event(
        self,
        event_id: str,
        *,
        summary: str = "Completed",
        sync: list[str] | None = None,
    ) -> tuple[EventCompletion, bool]:
        """Mark an event completed; idempotent if already completed.

        Returns ``(completion, already_completed)``.

        When already completed, requested ``sync`` targets that previously failed
        (or were never attempted) are retried; successful syncs are not repeated.
        The original summary is preserved on retry.
        """
        lock = await self._completion_lock(event_id)
        async with lock:
            event = await self.store.get_event(event_id)
            if event is None:
                raise KeyError(f"Event '{event_id}' not found")

            targets = list(sync or [])
            existing = await self.store.get_state(event_id, COMPLETION_STATE_KEY)
            if existing is not None and existing.get("status") == "completed":
                return await self._retry_failed_sync(
                    event,
                    EventCompletion.model_validate(existing),
                    targets,
                )

            sync_results = await self._run_sync(event, summary, targets)
            completion = EventCompletion(
                status="completed",
                summary=summary,
                completed_at=current_iso_time(),
                sync=targets,
                sync_results=sync_results,
            )
            await self.store.put_state(event_id, COMPLETION_STATE_KEY, completion.as_state())
            await self.store.put_state(
                event_id,
                EXECUTION_STATE_KEY,
                {
                    "status": "completed",
                    "progress": 100,
                    "summary": summary,
                    "completed_at": completion.completed_at,
                },
            )
            logger.info(
                "event.completed id={} summary={!r} sync={}",
                event_id,
                summary,
                targets,
            )
            return completion, False

    async def _retry_failed_sync(
        self,
        event: CloudEvent,
        completion: EventCompletion,
        targets: list[str],
    ) -> tuple[EventCompletion, bool]:
        """Re-run only failed/missing sync targets; keep original summary."""
        if not targets:
            return completion, True

        prior = {r.target: r for r in completion.sync_results}
        retry_targets = [t for t in targets if t not in prior or not prior[t].ok]
        if not retry_targets:
            return completion, True

        new_results = await self._run_sync(event, completion.summary, retry_targets)
        merged = dict(prior)
        for result in new_results:
            merged[result.target] = result
        sync_names = list(dict.fromkeys([*completion.sync, *targets]))
        updated = EventCompletion(
            status="completed",
            summary=completion.summary,
            completed_at=completion.completed_at,
            sync=sync_names,
            sync_results=list(merged.values()),
        )
        await self.store.put_state(event.id, COMPLETION_STATE_KEY, updated.as_state())
        logger.info(
            "event.sync_retry id={} targets={}",
            event.id,
            retry_targets,
        )
        return updated, True

    async def _run_sync(
        self,
        event: CloudEvent,
        summary: str,
        targets: list[str],
    ) -> list[SyncResult]:
        results: list[SyncResult] = []
        for target in targets:
            if target == "telegram":
                outbound = TelegramOutbound(self.store, self.settings)
                try:
                    results.append(await outbound.sync_completion(event, summary))
                finally:
                    await outbound.aclose()
            else:
                results.append(
                    SyncResult(
                        target=target,
                        ok=False,
                        detail=f"Unknown sync target '{target}' (supported: telegram)",
                    )
                )
        return results

    async def event_detail(self, event_id: str) -> dict[str, Any] | None:
        """CloudEvent dump plus completion / execution state (for GET APIs)."""
        event = await self.store.get_event(event_id)
        if event is None:
            return None
        detail = event.model_dump()
        detail["completion"] = await self.store.get_state(event_id, COMPLETION_STATE_KEY)
        detail["execution_state"] = await self.store.get_state(event_id, EXECUTION_STATE_KEY)
        return detail


__all__ = ["Engine"]
