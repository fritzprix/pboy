"""Telegram Bot API outbound (sendMessage / sendPhoto / sendDocument / …)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from pboy.channels.base import redact_secret
from pboy.channels.telegram import DEFAULT_API_BASE, TELEGRAM_SECRET_KEY
from pboy.core.config import Settings
from pboy.models.cloudevent import CloudEvent
from pboy.models.completion import SyncResult
from pboy.models.reply_file import (
    MAX_UPLOAD_BYTES,
    ReplyFile,
    ReplyFileType,
    resolve_reply_file_path,
    truncate_telegram_caption,
)
from pboy.store.base import StoreBase

_SEND_METHOD: dict[ReplyFileType, str] = {
    "photo": "sendPhoto",
    "document": "sendDocument",
    "audio": "sendAudio",
    "video": "sendVideo",
}
_SEND_FIELD: dict[ReplyFileType, str] = {
    "photo": "photo",
    "document": "document",
    "audio": "audio",
    "video": "video",
}


class TelegramOutbound:
    """Post a completion summary (and optional reply file) to the originating chat."""

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

    def _files_root(self) -> Path:
        return Path(self._settings.data_dir) / "files"

    async def sync_completion(
        self,
        event: CloudEvent,
        summary: str,
        *,
        reply_file: ReplyFile | None = None,
    ) -> SyncResult:
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

        reply_to = self._reply_to_message_id(event)
        if reply_file is not None:
            return await self._send_file(
                token=token,
                chat_id=chat_id,
                summary=summary,
                reply_to=reply_to,
                reply_file=reply_file,
            )
        return await self._send_message(
            token=token,
            chat_id=chat_id,
            summary=summary,
            reply_to=reply_to,
        )

    @staticmethod
    def _reply_to_message_id(event: CloudEvent) -> int | None:
        entity_key = event.data.get("entity_key")
        if isinstance(entity_key, str) and entity_key.isdigit():
            return int(entity_key)
        if isinstance(entity_key, int):
            return entity_key
        return None

    async def _send_message(
        self,
        *,
        token: str,
        chat_id: Any,
        summary: str,
        reply_to: int | None,
    ) -> SyncResult:
        body: dict[str, Any] = {"chat_id": chat_id, "text": summary}
        if reply_to is not None:
            body["reply_to_message_id"] = reply_to
        url = f"{self._api_base}/bot{token}/sendMessage"
        try:
            res = await self._client.post(url, json=body)
            res.raise_for_status()
            payload = res.json()
        except httpx.HTTPError as exc:
            msg = redact_secret(str(exc), token)
            logger.warning("outbound.telegram sendMessage failed: {}", msg)
            return SyncResult(target=self.target, ok=False, detail=msg)
        except ValueError as exc:
            return SyncResult(
                target=self.target,
                ok=False,
                detail=f"Telegram sendMessage invalid JSON: {exc}",
            )
        return self._parse_send_result(payload, method="sendMessage")

    async def _send_file(
        self,
        *,
        token: str,
        chat_id: Any,
        summary: str,
        reply_to: int | None,
        reply_file: ReplyFile,
    ) -> SyncResult:
        method = _SEND_METHOD[reply_file.file_type]
        field = _SEND_FIELD[reply_file.file_type]
        url = f"{self._api_base}/bot{token}/{method}"
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        caption = truncate_telegram_caption(summary.strip())
        if caption:
            data["caption"] = caption
        if reply_to is not None:
            data["reply_to_message_id"] = str(reply_to)

        try:
            if reply_file.file_path:
                path = resolve_reply_file_path(
                    reply_file.file_path, files_root=self._files_root()
                )
                size = path.stat().st_size
                if size > MAX_UPLOAD_BYTES:
                    return SyncResult(
                        target=self.target,
                        ok=False,
                        detail=(
                            f"Reply file too large ({size} > {MAX_UPLOAD_BYTES} bytes)"
                        ),
                    )
                with path.open("rb") as handle:
                    files = {field: (path.name, handle)}
                    res = await self._client.post(url, data=data, files=files)
            else:
                file_id = reply_file.file_id
                if not file_id:
                    return SyncResult(
                        target=self.target,
                        ok=False,
                        detail="ReplyFile missing file_id",
                    )
                data[field] = file_id
                res = await self._client.post(url, data=data)
            res.raise_for_status()
            payload = res.json()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return SyncResult(target=self.target, ok=False, detail=str(exc))
        except httpx.HTTPError as exc:
            msg = redact_secret(str(exc), token)
            logger.warning("outbound.telegram {} failed: {}", method, msg)
            return SyncResult(target=self.target, ok=False, detail=msg)
        except ValueError as exc:
            return SyncResult(
                target=self.target,
                ok=False,
                detail=f"Telegram {method} invalid JSON: {exc}",
            )
        return self._parse_send_result(payload, method=method)

    def _parse_send_result(self, payload: Any, *, method: str) -> SyncResult:
        if not isinstance(payload, dict) or not payload.get("ok"):
            return SyncResult(
                target=self.target,
                ok=False,
                detail=f"Telegram {method} not ok: {payload!r}",
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
