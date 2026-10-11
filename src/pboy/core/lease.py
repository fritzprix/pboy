"""Concurrency and lease engine for agent harness execution."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from pboy.models.cloudevent import current_iso_time


@dataclass
class LeaseToken:
    """Represents an active concurrency lease for a hook."""

    token: str
    hook_name: str
    acquired_at: str = field(default_factory=current_iso_time)
    created_at_monotonic: float = field(default_factory=time.monotonic)
    ttl_seconds: float | None = None

    def is_expired(self, now_mono: float | None = None) -> bool:
        """Check if lease has expired its TTL."""
        if self.ttl_seconds is None or self.ttl_seconds <= 0:
            return False
        now = time.monotonic() if now_mono is None else now_mono
        return (now - self.created_at_monotonic) >= self.ttl_seconds


class LeaseEngine:
    """Controls execution slots and concurrency limits per hook."""

    def __init__(self, default_ttl_seconds: float | None = None) -> None:
        self._lock = asyncio.Lock()
        self._active_leases: dict[str, LeaseToken] = {}  # token -> LeaseToken
        self._default_ttl_seconds = default_ttl_seconds

    def _prune_expired_locked(self) -> None:
        """Prune expired leases. Caller must hold self._lock."""
        now = time.monotonic()
        expired = [
            tok for tok, lease in self._active_leases.items()
            if lease.is_expired(now)
        ]
        for tok in expired:
            self._active_leases.pop(tok, None)

    async def acquire(
        self,
        hook_name: str,
        max_concurrency: int = 1,
        ttl_seconds: float | None = None,
    ) -> str | None:
        """Attempt to acquire an execution lease. Returns lease token string or None."""
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        async with self._lock:
            self._prune_expired_locked()
            current = self.active_count(hook_name)
            if current >= max_concurrency:
                return None

            token_str = f"lease_{uuid.uuid4().hex[:12]}"
            self._active_leases[token_str] = LeaseToken(
                token=token_str,
                hook_name=hook_name,
                ttl_seconds=effective_ttl,
            )
            return token_str

    async def release(self, token: str) -> bool:
        """Release a previously acquired lease token."""
        async with self._lock:
            lease = self._active_leases.pop(token, None)
            return lease is not None

    def active_count(self, hook_name: str) -> int:
        """Get the number of currently active, non-expired leases for a hook."""
        now = time.monotonic()
        return sum(
            1 for lease in self._active_leases.values()
            if lease.hook_name == hook_name and not lease.is_expired(now)
        )


__all__ = [
    "LeaseEngine",
    "LeaseToken",
    "current_iso_time",
]
