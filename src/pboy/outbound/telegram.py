"""Telegram Bot API outbound (sendMessage)."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from pboy.channels.telegram import DEFAULT_API_BASE, TELEGRAM_SECRET_KEY, redact_secret
from pboy.core.config import Settings
from pboy.models.cloudevent import CloudEvent
from pboy.models.completion import SyncResult
from pboy.store.base import StoreBase


class TelegramOutbound:
    """Post a completion summary back to the originating Telegram chat."""

    target = "telegram"

    def __init__(
        self,
        store: StoreBase,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        self._store = store
        self._settings = settings
        self._api_base = api_base.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _resolve_token(self) -> str:
        env_token = self._settings.telegram_bot_token.strip()
        if env_token:
            return env_token
        stored = await self._store.get_secret(TELEGRAM_SECRET_KEY)
        if stored and stored.strip():
            return stored.strip()
        raise RuntimeError(
            "Telegram bot token missing for outbound sync "
            f"(PB_TELEGRAM_BOT_TOKEN or secret '{TELEGRAM_SECRET_KEY}')"
        )

    async def sync_completion(self, event: CloudEvent, summary: str) -> SyncResult:
        chat_id = event.data.get("channel_id")
        if chat_id is None or str(chat_id).strip() == "":
            return SyncResult(
                target=self.target,
                ok=False,
                detail="event.data.channel_id missing (Telegram chat id)",
            )

        try:
            token = await self._resolve_token()
        except RuntimeError as exc:
            return SyncResult(target=self.target, ok=False, detail=str(exc))

        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": summary,
        }
        entity_key = event.data.get("entity_key")
        if isinstance(entity_key, str) and entity_key.isdigit():
            body["reply_to_message_id"] = int(entity_key)
        elif isinstance(entity_key, int):
            body["reply_to_message_id"] = entity_key

        url = f"{self._api_base}/bot{token}/sendMessage"
        try:
            res = await self._client.post(url, json=body)
            res.raise_for_status()
            payload = res.json()
        except httpx.HTTPError as exc:
            msg = redact_secret(str(exc), token)
            logger.warning("outbound.telegram sendMessage failed: {}", msg)
            return SyncResult(target=self.target, ok=False, detail=msg)

        if not isinstance(payload, dict) or not payload.get("ok"):
            return SyncResult(
                target=self.target,
                ok=False,
                detail=f"Telegram sendMessage not ok: {payload!r}",
            )

        result_obj = payload.get("result")
        result = result_obj if isinstance(result_obj, dict) else {}
        message_id = result.get("message_id")
        return SyncResult(
            target=self.target,
            ok=True,
            detail="sent",
            message_id=str(message_id) if message_id is not None else None,
        )


__all__ = ["TelegramOutbound"]
