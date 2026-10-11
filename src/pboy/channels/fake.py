"""In-process fake channel for tests and local demos (no network)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from pboy.channels.base import EventEmitter


class FakeChannel:
    """Test/demo adapter: ``push`` injects a raw payload through the Engine sink."""

    def __init__(
        self,
        emit: EventEmitter,
        *,
        source: str = "jira",
        name: str = "fake",
    ) -> None:
        self._emit = emit
        self._source = source
        self._name = name
        self._started = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def source(self) -> str:
        return self._source

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        logger.info("channel.connect name={} source={}", self._name, self._source)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        logger.info("channel.disconnect name={} source={}", self._name, self._source)

    async def push(self, payload: dict[str, Any], *, dispatch: bool = True) -> Any:
        """Emit a raw platform payload into the Engine (adapter must be started)."""
        if not self._started:
            raise RuntimeError(f"Channel '{self._name}' is not started")
        return await self._emit(payload, self._source, dispatch=dispatch)


__all__ = ["FakeChannel"]
