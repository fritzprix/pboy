"""HTTP client for daemon-backed CLI operations."""

from __future__ import annotations

import os
from typing import Any

import httpx

from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook, HookCreate


def resolve_daemon_url() -> str | None:
    """Return PB_DAEMON_URL when explicitly set; otherwise None (local SQLite mode)."""
    raw = os.environ.get("PB_DAEMON_URL", "").strip()
    return raw or None


def resolve_auth_token() -> str | None:
    """Return PB_AUTH_TOKEN when set."""
    raw = os.environ.get("PB_AUTH_TOKEN", "").strip()
    return raw or None


class DaemonClient:
    """Thin sync client for daemon HTTP APIs."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        auth_token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_token = auth_token if auth_token is not None else resolve_auth_token()
        self._client = client

    def _headers(self) -> dict[str, str]:
        if not self.auth_token:
            return {}
        return {"X-PB-Token": self.auth_token}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._headers())
        url = f"{self.base_url}{path}"
        if self._client is not None:
            return self._client.request(method, path, headers=headers, timeout=self.timeout, **kwargs)
        return httpx.request(method, url, headers=headers, timeout=self.timeout, **kwargs)

    def put_state(self, event_id: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "PUT",
            f"/v1/events/{event_id}/state",
            json={"key": key, "value": value},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def get_state(self, event_id: str, key: str = "execution") -> dict[str, Any] | None:
        response = self._request(
            "GET",
            f"/v1/events/{event_id}/state",
            params={"key": key},
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
        response = self._request(
            "GET",
            "/v1/events",
            params={"limit": limit, "offset": offset},
        )
        response.raise_for_status()
        body = response.json()
        events_raw = body.get("events", [])
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
        response = self._request("GET", f"/v1/events/{event_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return CloudEvent.model_validate(response.json())

    def list_hooks(self) -> list[Hook]:
        response = self._request("GET", "/v1/hooks")
        response.raise_for_status()
        return [Hook.model_validate(item) for item in response.json().get("hooks", [])]

    def create_hook(self, data: HookCreate) -> Hook:
        response = self._request("POST", "/v1/hooks", json=data.model_dump())
        response.raise_for_status()
        return Hook.model_validate(response.json())

    def delete_hook(self, name: str) -> bool:
        response = self._request("DELETE", f"/v1/hooks/{name}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return bool(response.json().get("deleted"))
