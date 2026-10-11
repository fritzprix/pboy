"""Channel adapter protocol — inbound platforms push raw events into the Engine."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventEmitter(Protocol):
    """Sink used by adapters to hand raw platform payloads to the Engine."""

    async def __call__(
        self,
        raw_payload: dict[str, Any],
        source: str,
        *,
        dispatch: bool = True,
    ) -> Any: ...


@runtime_checkable
class ChannelAdapter(Protocol):
    """Long-running inbound receiver (poll/socket/webhook consumer)."""

    @property
    def name(self) -> str:
        """Stable adapter id (e.g. ``telegram``, ``fake``)."""
        ...

    async def start(self) -> None:
        """Begin receiving (idempotent preferred)."""
        ...

    async def stop(self) -> None:
        """Stop receiving and release resources (idempotent preferred)."""
        ...


__all__ = ["ChannelAdapter", "EventEmitter"]
