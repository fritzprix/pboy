"""Slack Web API outbound (``chat.postMessage`` thread reply)."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from pboy.channels.base import redact_secret
from pboy.channels.slack import SLACK_API_BASE, SLACK_BOT_SECRET_KEY
from pboy.core.config import Settings
from pboy.models.cloudevent import CloudEvent
from pboy.models.completion import SyncResult
from pboy.models.reply_file import ReplyFile
from pboy.store.base import StoreBase


def _looks_like_slack_ts(value: Any) -> bool:
    """Slack message timestamps look like ``1696800000.000100``."""
    if isinstance(value, (int, float)):
        return False
    text = str(value).strip()
    if "." not in text:
        return False
    left, _, right = text.partition(".")
    return left.isdigit() and right.isdigit()


class SlackOutbound:
    """Post a completion summary into the originating Slack channel (threaded)."""

    target = "slack"

    def __init__(
        self,
        store: StoreBase,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = SLACK_API_BASE,
    ) -> None:
        self._store = store
        self._settings = settings
        self._api_base = api_base.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _resolve_bot_token(self) -> str:
        stored = await self._store.get_secret(SLACK_BOT_SECRET_KEY)
        if stored and stored.strip():
            return stored.strip()
        raise RuntimeError(
            "Slack bot token missing for outbound sync "
            f"(configure in /ui/settings or store secret '{SLACK_BOT_SECRET_KEY}')"
        )

    @staticmethod
    def _thread_ts(event: CloudEvent) -> str | None:
        """Prefer real thread_id; else message ``ts`` from ``extra`` / entity_key."""
        data = event.data
        thread_id = data.get("thread_id")
        if isinstance(thread_id, str) and thread_id.strip():
            return thread_id.strip()
        extra = data.get("extra")
        if isinstance(extra, dict):
            ts = extra.get("ts")
            if _looks_like_slack_ts(ts):
                return str(ts).strip()
        entity_key = data.get("entity_key")
        if _looks_like_slack_ts(entity_key):
            return str(entity_key).strip()
        return None

    async def sync_completion(
        self,
        event: CloudEvent,
        summary: str,
        *,
        reply_file: ReplyFile | None = None,
    ) -> SyncResult:
        if reply_file is not None:
            return SyncResult(
                target=self.target,
                ok=False,
                detail=(
                    "Slack sync does not support --file-* yet; "
                    "send text with --sync slack / event reply only"
                ),
            )

        channel = event.data.get("channel_id")
        if channel is None or str(channel).strip() == "":
            return SyncResult(
                target=self.target,
                ok=False,
                detail="event.data.channel_id missing (Slack channel id)",
            )

        try:
            token = await self._resolve_bot_token()
        except RuntimeError as exc:
            return SyncResult(target=self.target, ok=False, detail=str(exc))

        body: dict[str, Any] = {
            "channel": str(channel).strip(),
            "text": summary,
        }
        thread_ts = self._thread_ts(event)
        if thread_ts:
            body["thread_ts"] = thread_ts

        url = f"{self._api_base}/chat.postMessage"
        try:
            res = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            res.raise_for_status()
            payload = res.json()
        except httpx.HTTPError as exc:
            msg = redact_secret(str(exc), token)
            logger.warning("outbound.slack chat.postMessage failed: {}", msg)
            return SyncResult(target=self.target, ok=False, detail=msg)
        except ValueError as exc:
            return SyncResult(
                target=self.target,
                ok=False,
                detail=f"Slack chat.postMessage invalid JSON: {exc}",
            )

        if not isinstance(payload, dict) or not payload.get("ok"):
            err = payload.get("error") if isinstance(payload, dict) else payload
            return SyncResult(
                target=self.target,
                ok=False,
                detail=f"Slack chat.postMessage not ok: {err!r}",
            )
        message_ts = payload.get("ts")
        return SyncResult(
            target=self.target,
            ok=True,
            detail="sent",
            message_id=str(message_ts) if message_ts is not None else None,
        )


__all__ = ["SlackOutbound"]
