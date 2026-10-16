"""Telegram Bot API long-polling inbound channel."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from pboy.channels.base import EventEmitter, redact_secret
from pboy.channels.telegram_files import download_telegram_file
from pboy.core.config import Settings
from pboy.core.telegram_media import (
    extract_telegram_attachments,
    reset_local_files,
    set_local_files,
)
from pboy.store.base import StoreBase

TELEGRAM_SECRET_KEY = "telegram.bot_token"
TELEGRAM_ALLOWLIST_SECRET_KEY = "telegram.allowlist"
DEFAULT_API_BASE = "https://api.telegram.org"
# Cap concurrent download+emit work so getUpdates stays responsive.
_MAX_INFLIGHT_EMITS = 4
PAIRING_TTL_SECONDS = 300.0


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Parse comma/whitespace-separated Telegram chat/user IDs into a set of strings."""
    if not raw:
        return frozenset()
    items: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        token = part.strip()
        if token:
            items.add(token)
    return frozenset(items)


def _extract_message_ids(message: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(chat_id, from_id)`` as strings when present on a Telegram message dict."""
    chat = message.get("chat")
    sender = message.get("from")
    chat_id = (
        str(chat["id"])
        if isinstance(chat, dict) and chat.get("id") is not None
        else None
    )
    from_id = (
        str(sender["id"])
        if isinstance(sender, dict) and sender.get("id") is not None
        else None
    )
    return chat_id, from_id


class TelegramChannel:
    """Long-poll ``getUpdates`` and emit each update into the Engine."""

    name = "telegram"

    def __init__(
        self,
        emit: EventEmitter,
        store: StoreBase,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = DEFAULT_API_BASE,
        poll_timeout: int = 25,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self._emit = emit
        self._store = store
        self._settings = settings
        self._api_base = api_base.rstrip("/")
        self._poll_timeout = poll_timeout
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(poll_timeout + 10.0))
        self._token: str | None = None
        self._offset = 0
        self._task: asyncio.Task[None] | None = None
        self._emit_tasks: set[asyncio.Task[None]] = set()
        self._emit_sem = asyncio.Semaphore(_MAX_INFLIGHT_EMITS)
        self._started = False
        self._pairing_until: float = 0.0
        # code -> (chat_id, from_id, expires_at)
        self._pairing_codes: dict[str, tuple[str | None, str | None, float]] = {}
        self._last_seen_peer: tuple[str | None, str | None] = (None, None)

    @property
    def started(self) -> bool:
        return self._started

    @property
    def pairing_active(self) -> bool:
        return time.monotonic() < self._pairing_until

    def _prune_expired_codes(self) -> None:
        now = time.monotonic()
        expired = [c for c, (_, _, exp) in self._pairing_codes.items() if exp <= now]
        for c in expired:
            self._pairing_codes.pop(c, None)

    def _mint_code_for_peer(self, chat_id: str | None, from_id: str | None) -> str:
        self._prune_expired_codes()
        for _ in range(20):
            code = f"{secrets.randbelow(900000) + 100000:06d}"
            if code not in self._pairing_codes:
                break
        expires = max(self._pairing_until, time.monotonic() + PAIRING_TTL_SECONDS)
        self._pairing_codes[code] = (chat_id, from_id, expires)
        return code

    async def _send_pairing_message(self, chat_id: str, code: str) -> bool:
        """Send the 6-digit pairing code to ``chat_id`` via Bot API."""
        try:
            if not self._token:
                self._token = await self._resolve_token()
            res = await self._client.post(
                self._bot_url("sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": f"Pageboy 인증 코드: {code}\n(설정 화면에 이 6자리 숫자를 입력하세요)",
                },
            )
            return res.status_code < 400 and bool(res.json().get("ok"))
        except Exception as exc:
            logger.warning(
                "channel.telegram pairing sendMessage failed: {}",
                redact_secret(str(exc), self._token),
            )
            return False

    async def start_pairing(self, *, ttl: float = PAIRING_TTL_SECONDS) -> dict[str, Any]:
        """Open a pairing window; if a recent chat is known, send a code immediately."""
        self._pairing_until = time.monotonic() + ttl
        self._prune_expired_codes()
        chat_id, from_id = self._last_seen_peer
        sent_immediately = False
        if chat_id:
            code = self._mint_code_for_peer(chat_id, from_id)
            sent_immediately = await self._send_pairing_message(chat_id, code)
        return {
            "pairing_active": True,
            "ttl_seconds": int(ttl),
            "sent_immediately": sent_immediately,
        }

    async def verify_pairing_code(self, code: str) -> list[str]:
        """Verify a 6-digit pairing code and append its chat_id/from_id to ``telegram.allowlist``."""
        self._prune_expired_codes()
        cleaned = code.strip()
        entry = self._pairing_codes.pop(cleaned, None)
        if entry is None:
            raise ValueError("유효하지 않거나 만료된 인증 코드입니다.")
        chat_id, from_id, _exp = entry
        new_ids: list[str] = []
        for candidate in (chat_id, from_id):
            if candidate and candidate not in new_ids:
                new_ids.append(candidate)
        if not new_ids:
            raise ValueError("대화방 ID를 확인할 수 없습니다.")

        existing_raw = await self._store.get_secret(TELEGRAM_ALLOWLIST_SECRET_KEY)
        merged = list(parse_allowlist(existing_raw))
        for cid in new_ids:
            if cid not in merged:
                merged.append(cid)
        merged_sorted = sorted(merged)
        await self._store.put_secret(TELEGRAM_ALLOWLIST_SECRET_KEY, ",".join(merged_sorted))
        self._pairing_until = 0.0
        logger.info("channel.telegram paired ids={}", new_ids)
        return merged_sorted

    async def _resolve_token(self) -> str:
        env_token = self._settings.telegram_bot_token.strip()
        if env_token:
            return env_token
        stored = await self._store.get_secret(TELEGRAM_SECRET_KEY)
        if stored and stored.strip():
            return stored.strip()
        raise RuntimeError(
            "Telegram bot token missing. Set PB_TELEGRAM_BOT_TOKEN or store secret "
            f"'{TELEGRAM_SECRET_KEY}' (see README / channels docs)."
        )

    async def _resolve_allowlist(self) -> frozenset[str]:
        """Resolve allowed chat/user IDs (non-empty env wins over store; empty = allow all)."""
        env_raw = self._settings.telegram_allowlist.strip()
        if env_raw:
            return parse_allowlist(env_raw)
        stored = await self._store.get_secret(TELEGRAM_ALLOWLIST_SECRET_KEY)
        return parse_allowlist(stored)

    async def _is_allowed(self, message: dict[str, Any], update_id: int | None) -> bool:
        chat_id, from_id = _extract_message_ids(message)
        if chat_id or from_id:
            self._last_seen_peer = (chat_id, from_id)

        # While pairing mode is active, reply with a 6-digit code to any chat message.
        if self.pairing_active and chat_id:
            code = self._mint_code_for_peer(chat_id, from_id)
            await self._send_pairing_message(chat_id, code)
            text = str(message.get("text") or "").strip()
            # If the message is "/start" or "/pair" or sender is not yet allowed,
            # consume the update for pairing without dispatching hooks.
            allowlist_now = await self._resolve_allowlist()
            is_in_allowlist = not allowlist_now or (
                (chat_id in allowlist_now) or (bool(from_id) and from_id in allowlist_now)
            )
            if not is_in_allowlist or text.startswith("/start") or text.startswith("/pair"):
                return False

        allowlist = await self._resolve_allowlist()
        if not allowlist:
            return True
        if (chat_id and chat_id in allowlist) or (from_id and from_id in allowlist):
            return True
        logger.warning(
            "channel.telegram drop update_id={} chat_id={} from_id={} (not in allowlist)",
            update_id,
            chat_id,
            from_id,
        )
        return False

    async def start(self) -> None:
        if self._started:
            return
        self._token = await self._resolve_token()
        await self._delete_webhook()
        self._started = True
        self._task = asyncio.create_task(self._poll_loop(), name="pboy-telegram-poll")
        logger.info("channel.connect name=telegram transport=long-poll")

    async def stop(self) -> None:
        if not self._started and self._task is None:
            return
        self._started = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Do not cancel in-flight emits: offset is already advanced; wait briefly.
        pending = list(self._emit_tasks)
        if pending:
            _done, still = await asyncio.wait(pending, timeout=30.0)
            for task in still:
                task.cancel()
            if still:
                await asyncio.gather(*still, return_exceptions=True)
        self._emit_tasks.clear()
        if self._owns_client:
            await self._client.aclose()
        logger.info("channel.disconnect name=telegram")

    def _bot_url(self, method: str) -> str:
        if not self._token:
            raise RuntimeError("Telegram token not resolved")
        return f"{self._api_base}/bot{self._token}/{method}"

    async def _delete_webhook(self) -> None:
        """Clear webhook so getUpdates long-poll is exclusive."""
        try:
            res = await self._client.post(
                self._bot_url("deleteWebhook"), json={"drop_pending_updates": False}
            )
            if res.status_code >= 400:
                logger.warning(
                    "channel.telegram deleteWebhook status={} body={}",
                    res.status_code,
                    res.text[:200],
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "channel.telegram deleteWebhook failed: {}",
                redact_secret(str(exc), self._token),
            )

    async def _fetch_updates(self) -> list[dict[str, Any]]:
        try:
            res = await self._client.get(
                self._bot_url("getUpdates"),
                params={"offset": self._offset, "timeout": self._poll_timeout},
            )
            res.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(redact_secret(str(exc), self._token)) from exc
        body = res.json()
        if not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError(f"Telegram getUpdates not ok: {body!r}")
        result = body.get("result")
        if not isinstance(result, list):
            return []
        updates: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                updates.append(item)
        return updates

    def _files_dir(self, update_id: int | None) -> Path:
        stamp = str(update_id) if isinstance(update_id, int) else "unknown"
        return Path(self._settings.data_dir) / "files" / "telegram" / stamp

    async def _download_local_files(
        self, payload: dict[str, Any], update_id: int | None
    ) -> dict[str, str]:
        """Download inbound files when enabled; return file_id → local_path."""
        if not self._settings.telegram_download_files:
            return {}
        message = payload.get("message")
        if not isinstance(message, dict):
            return {}
        attachments = extract_telegram_attachments(message)
        if not attachments:
            return {}
        if not self._token:
            try:
                self._token = await self._resolve_token()
            except RuntimeError:
                logger.warning("channel.telegram skip download (token missing)")
                return {}

        local_map: dict[str, str] = {}
        dest_dir = self._files_dir(update_id)
        for att in attachments:
            try:
                path = await download_telegram_file(
                    self._client,
                    token=self._token,
                    api_base=self._api_base,
                    file_id=att.file_id,
                    dest_dir=dest_dir,
                    file_type=att.file_type,
                    file_name=att.file_name,
                )
                local_map[att.file_id] = str(path)
            except Exception as exc:
                logger.warning(
                    "channel.telegram download_failed file_id={} err={}",
                    att.file_id[:16],
                    redact_secret(str(exc), self._token),
                )
        return local_map

    def _schedule_process(
        self,
        payload: dict[str, Any],
        update_id: int | None,
        *,
        dispatch: bool,
    ) -> None:
        task = asyncio.create_task(
            self._process_safe(payload, update_id, dispatch=dispatch),
            name="pboy-telegram-emit",
        )
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)

    async def _process_safe(
        self,
        payload: dict[str, Any],
        update_id: int | None,
        *,
        dispatch: bool,
    ) -> None:
        async with self._emit_sem:
            try:
                await self._emit_with_downloads(payload, update_id, dispatch=dispatch)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("channel.telegram emit_failed")

    async def _emit_with_downloads(
        self,
        payload: dict[str, Any],
        update_id: int | None,
        *,
        dispatch: bool,
    ) -> Any:
        local_map = await self._download_local_files(payload, update_id)
        token = set_local_files(local_map or None)
        try:
            return await self._emit(payload, "telegram", dispatch=dispatch)
        finally:
            reset_local_files(token)

    async def handle_update(
        self,
        update: dict[str, Any],
        *,
        dispatch: bool = True,
        background: bool = False,
    ) -> Any | None:
        """Process a single Telegram update dict (public for tests)."""
        update_id = update.get("update_id")
        if isinstance(update_id, int) and update_id >= self._offset:
            self._offset = update_id + 1

        # Only message / edited_message / channel_post style payloads normalize cleanly.
        if not any(k in update for k in ("message", "edited_message", "channel_post")):
            logger.debug("channel.telegram skip update_id={} (no message payload)", update_id)
            return None

        # Prefer message-shaped payload for the existing normalizer.
        raw_msg: Any
        if "message" in update:
            raw_msg = update["message"]
        elif "edited_message" in update:
            raw_msg = update["edited_message"]
        else:
            raw_msg = update["channel_post"]

        uid = update_id if isinstance(update_id, int) else None
        msg_dict = raw_msg if isinstance(raw_msg, dict) else {}
        if not await self._is_allowed(msg_dict, uid):
            return None

        payload: dict[str, Any] = {"message": raw_msg}
        if background:
            # Download + emit off the poll loop (bounded by ``_emit_sem``).
            self._schedule_process(payload, uid, dispatch=dispatch)
            return None
        return await self._emit_with_downloads(payload, uid, dispatch=dispatch)

    async def _poll_loop(self) -> None:
        backoff = self._initial_backoff
        while self._started:
            try:
                updates = await self._fetch_updates()
                backoff = self._initial_backoff
                for update in updates:
                    try:
                        await self.handle_update(update, background=True)
                    except Exception:
                        logger.exception(
                            "channel.telegram handle_failed update_id={}",
                            update.get("update_id"),
                        )
                # Real getUpdates long-polls; mocks return immediately — yield so stop() can run.
                if not updates:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "channel.telegram poll_error err={} backoff={:.1f}s",
                    redact_secret(str(exc), self._token),
                    backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2.0, self._max_backoff)


def build_telegram_channel(
    emit: EventEmitter,
    store: StoreBase,
    settings: Settings,
) -> TelegramChannel:
    """Factory entry used by ``ChannelRegistry``."""
    return TelegramChannel(emit, store, settings)


__all__ = [
    "DEFAULT_API_BASE",
    "TELEGRAM_ALLOWLIST_SECRET_KEY",
    "TELEGRAM_SECRET_KEY",
    "TelegramChannel",
    "build_telegram_channel",
    "parse_allowlist",
    "redact_secret",
]
