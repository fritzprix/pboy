"""Core orchestrator engine: integrates Normalizer, Router, Lease, and Store."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from pboy.core.lease import LeaseEngine
from pboy.core.normalizer import EventNormalizer
from pboy.core.router import HookRouter
from pboy.harness.executor import HarnessExecutor
from pboy.models.cloudevent import CloudEvent
from pboy.models.execution import ExecutionData
from pboy.models.hook import Hook
from pboy.store.base import StoreBase
from pboy.store.sqlite import SQLiteStore


class Engine:
    """Central orchestrator for Pageboy."""

    def __init__(self, store: StoreBase | None = None) -> None:
        self.store = store or SQLiteStore()
        self.normalizer = EventNormalizer()
        self.router = HookRouter()
        self.lease = LeaseEngine()
        self.executor = HarnessExecutor(self.store)

    async def start(self) -> None:
        """Initialize engine resources and database."""
        await self.store.init_db()

    async def stop(self) -> None:
        """Release engine resources."""
        await self.store.close()

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


__all__ = ["Engine"]
