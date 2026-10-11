"""Unit tests for Engine dispatch edge cases: empty hooks, lease denied, and sequential execution."""

from __future__ import annotations

import pytest

from pboy.core.engine import Engine
from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook


@pytest.fixture
def test_event() -> CloudEvent:
    return CloudEvent(
        source="unit_test",
        type="test:action.triggered",
        data={"action": "reboot"},
    )


@pytest.mark.asyncio
async def test_engine_dispatch_empty_hooks(engine: Engine, test_event: CloudEvent) -> None:
    """Dispatching with empty hook list returns empty list immediately."""
    executions = await engine.dispatch(test_event, [])
    assert executions == []


@pytest.mark.asyncio
async def test_engine_dispatch_lease_denied(engine: Engine, test_event: CloudEvent) -> None:
    """When hook concurrency lease is saturated, dispatch skips execution gracefully."""
    hook = Hook(
        name="saturated-hook",
        topic="test:action.triggered",
        exec="echo saturated",
        concurrency=1,
    )
    # Pre-acquire the single available concurrency slot
    token = await engine.lease.acquire("saturated-hook", max_concurrency=1)
    assert token is not None

    try:
        # Dispatching should fail to acquire lease and return no executions
        executions = await engine.dispatch(test_event, [hook], concurrent=True)
        assert executions == []
    finally:
        await engine.lease.release(token)


@pytest.mark.asyncio
async def test_engine_dispatch_sequential(engine: Engine, test_event: CloudEvent) -> None:
    """When concurrent=False, hooks execute sequentially and lease-denied hooks are omitted."""
    hook_a = Hook(
        name="seq-hook-a",
        topic="test:action.triggered",
        exec="echo seq-a",
        concurrency=1,
    )
    hook_b = Hook(
        name="seq-hook-b",
        topic="test:action.triggered",
        exec="echo seq-b",
        concurrency=1,
    )
    hook_saturated = Hook(
        name="seq-hook-saturated",
        topic="test:action.triggered",
        exec="echo seq-sat",
        concurrency=1,
    )

    # Pre-acquire lease for saturated hook
    token = await engine.lease.acquire("seq-hook-saturated", max_concurrency=1)
    assert token is not None

    try:
        executions = await engine.dispatch(
            test_event,
            [hook_a, hook_saturated, hook_b],
            concurrent=False,
        )
        assert len(executions) == 2
        names = [e.hook_name for e in executions]
        assert names == ["seq-hook-a", "seq-hook-b"]
        for e in executions:
            assert e.status == "completed"
    finally:
        await engine.lease.release(token)
