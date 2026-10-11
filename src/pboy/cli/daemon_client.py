"""HTTP client for daemon-backed CLI operations."""

from __future__ import annotations

import os
from typing import Any

import httpx

from pboy.models.cloudevent import CloudEvent


def resolve_daemon_url() -> str | None:
    """Return PB_DAEMON_URL when explicitly set; otherwise None (local SQLite mode)."""
    raw = os.environ.get("PB_DAEMON_URL", "").strip()
    return raw or None


class DaemonClient:
    """Thin sync client for state/event APIs on the local daemon."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def put_state(self, event_id: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
        response = httpx.put(
            f"{self.base_url}/v1/events/{event_id}/state",
            json={"key": key, "value": value},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def get_state(self, event_id: str, key: str = "execution") -> dict[str, Any] | None:
        response = httpx.get(
            f"{self.base_url}/v1/events/{event_id}/state",
            params={"key": key},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        value = body.get("value")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("Daemon state value must be a JSON object")
        return value

    def list_events(self, limit: int = 20, offset: int = 0) -> list[CloudEvent]:
        response = httpx.get(
            f"{self.base_url}/v1/events",
            params={"limit": limit, "offset": offset},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        events_raw = body.get("events", [])
        # list endpoint returns summaries; fetch full objects when needed via get_event
        return [
            CloudEvent(
                id=item["id"],
                source=item["source"],
                type=item["type"],
                time=item["time"],
                data={},
            )
            for item in events_raw
        ]

    def get_event(self, event_id: str) -> CloudEvent | None:
        response = httpx.get(
            f"{self.base_url}/v1/events/{event_id}",
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return CloudEvent.model_validate(response.json())
