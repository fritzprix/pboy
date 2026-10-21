"""HTTP client for daemon-backed CLI operations."""

from __future__ import annotations

import os
from typing import Any

import httpx

from pboy.core.config import DEFAULT_HOST, DEFAULT_PORT, get_settings
from pboy.core.probe import probe_daemon_alive
from pboy.models.cloudevent import CloudEvent
from pboy.models.hook import Hook, HookCreate

DEFAULT_DAEMON_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

# Back-compat alias for tests / callers that patch `_probe_daemon_alive`.
_probe_daemon_alive = probe_daemon_alive


def resolve_daemon_url(
    cli_url: str | None = None,
    *,
    auto_probe: bool = True,
) -> str | None:
    """Return explicit ``--daemon-url``, or discover via runtime file, or None.

    Discovery order:
    1. Explicit ``--daemon-url`` wins
    2. ``PB_DATABASE_URL`` env var → None (targets a specific local DB)
    3. Runtime file in data dir → verify lock + /healthz token
    4. None (no daemon running)
    """
    if cli_url and cli_url.strip():
        return cli_url.strip().rstrip("/")
    if not auto_probe:
        return None
    # If an explicit PB_DATABASE_URL is set, the caller targets a specific local DB file.
    if os.environ.get("PB_DATABASE_URL", "").strip():
        return None

    from pboy.daemon.runtime import DaemonRuntimeFile

    settings = get_settings()
    runtime = DaemonRuntimeFile(settings.data_dir)
    info = runtime.read_if_alive()
    if not info:
        return None

    try:
        res = httpx.get(f"{info.base_url}/healthz", timeout=0.5)
        if res.status_code == 200:
            body = res.json()
            if body.get("service") == "pboy-daemon":
                token = body.get("instance_token", "")
                if token == info.instance_token:
                    return info.base_url
    except Exception:
        pass

    return None


class DaemonClient:
    """Thin sync client for daemon HTTP APIs."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
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

    def get_event_detail(self, event_id: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/v1/events/{event_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def complete_event(
        self,
        event_id: str,
        *,
        summary: str = "Completed",
        sync: list[str] | None = None,
        file_path: str | None = None,
        file_id: str | None = None,
        file_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"summary": summary, "sync": list(sync or [])}
        if file_path:
            payload["file_path"] = file_path
        if file_id:
            payload["file_id"] = file_id
        if file_type:
            payload["file_type"] = file_type
        response = self._request(
            "POST",
            f"/v1/events/{event_id}/complete",
            json=payload,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

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

    def list_channels(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/channels")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def enable_channel(self, name: str) -> dict[str, Any]:
        response = self._request("POST", f"/v1/channels/{name}/enable")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def disable_channel(self, name: str) -> dict[str, Any]:
        response = self._request("POST", f"/v1/channels/{name}/disable")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def set_channel_secret(
        self,
        name: str,
        value: str,
        *,
        secret_name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"value": value}
        if secret_name:
            payload["secret_name"] = secret_name
        response = self._request(
            "PUT",
            f"/v1/channels/{name}/secret",
            json=payload,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def healthz(self) -> dict[str, Any]:
        response = self._request("GET", "/healthz")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def inject_event(
        self,
        source: str,
        payload: dict[str, Any],
        *,
        dispatch: bool = True,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/events",
            json={"source": source, "payload": payload, "dispatch": dispatch},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def shutdown(self, instance_token: str) -> dict[str, Any]:
        """Request graceful daemon shutdown with token verification."""
        response = self._request(
            "POST",
            "/v1/daemon/shutdown",
            json={"instance_token": instance_token},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body
