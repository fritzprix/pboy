"""Abstract storage interface for Pageboy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pboy.models.cloudevent import CloudEvent
from pboy.models.execution import ExecutionData
from pboy.models.hook import Hook, HookCreate, HookUpdate


class StoreBase(ABC):
    """Abstract storage interface."""

    @abstractmethod
    async def init_db(self) -> None:
        """Initialize database tables and connections."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close database connection."""
        ...

    # Hooks
    @abstractmethod
    async def create_hook(self, data: HookCreate) -> Hook:
        """Register a new hook."""
        ...

    @abstractmethod
    async def get_hook(self, name: str) -> Hook | None:
        """Get hook by name."""
        ...

    @abstractmethod
    async def list_hooks(self) -> list[Hook]:
        """List all registered hooks."""
        ...

    @abstractmethod
    async def update_hook(self, name: str, data: HookUpdate) -> Hook | None:
        """Update an existing hook."""
        ...

    @abstractmethod
    async def delete_hook(self, name: str) -> bool:
        """Delete a hook by name."""
        ...

    # Events
    @abstractmethod
    async def create_event(self, event: CloudEvent) -> None:
        """Store a CloudEvent."""
        ...

    @abstractmethod
    async def get_event(self, event_id: str) -> CloudEvent | None:
        """Retrieve an event by ID."""
        ...

    @abstractmethod
    async def list_events(self, limit: int = 50, offset: int = 0) -> list[CloudEvent]:
        """List recent events."""
        ...

    # Executions
    @abstractmethod
    async def record_execution(self, exec_data: ExecutionData) -> None:
        """Record or update harness execution."""
        ...

    @abstractmethod
    async def get_execution(self, exec_id: str) -> ExecutionData | None:
        """Get execution details by ID."""
        ...

    @abstractmethod
    async def list_executions(self, limit: int = 50, offset: int = 0) -> list[ExecutionData]:
        """List harness executions."""
        ...

    # State
    @abstractmethod
    async def put_state(self, event_id: str, key: str, value: dict[str, Any]) -> None:
        """Store key-value state for an event."""
        ...

    @abstractmethod
    async def get_state(self, event_id: str, key: str) -> dict[str, Any] | None:
        """Retrieve state for an event and key."""
        ...

    # Secrets
    @abstractmethod
    async def put_secret(self, key: str, value: str) -> None:
        """Store encrypted secret value."""
        ...

    @abstractmethod
    async def get_secret(self, key: str) -> str | None:
        """Retrieve and decrypt secret value."""
        ...
