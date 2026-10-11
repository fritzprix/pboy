"""Slack Socket Mode inbound channel (no public webhook URL)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Final

import httpx
import websockets
from loguru import logger

from pboy.channels.base import EventEmitter, redact_secret
from pboy.core.config import Settings
from pboy.store.base import StoreBase

SLACK_BOT_SECRET_KEY = "slack.bot_token"
SLACK_APP_SECRET_KEY = "slack.app_token"
SLACK_API_BASE = "https://slack.com/api"

# Returned by handle_envelope when Slack requests a Socket Mode rollover.
_RECONNECT: Final[object] = object()


class SlackChannel:
    """Connect via Socket Mode and emit Events API message payloads."""

    name = "slack"
    RECONNECT: Final[object] = _RECONNECT

    def __init__(
        self,
        emit: EventEmitter,
        store: StoreBase,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = SLACK_API_BASE,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self._emit = emit
        self._store = store
        self._settings = settings
        self._api_base = api_base.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._bot_token: str | None = None
        self._app_token: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._emit_tasks: set[asyncio.Task[Any]] = set()
        self._started = False
        self._ws: Any | None = None

    @property
    def started(self) -> bool:
        return self._started

    async def _resolve_tokens(self) -> tuple[str, str]:
        bot = self._settings.slack_bot_token.strip()
        app = self._settings.slack_app_token.strip()
        if not bot:
            stored = await self._store.get_secret(SLACK_BOT_SECRET_KEY)
            bot = stored.strip() if stored else ""
        if not app:
            stored = await self._store.get_secret(SLACK_APP_SECRET_KEY)
            app = stored.strip() if stored else ""
        if not bot or not app:
            raise RuntimeError(
                "Slack tokens missing. Set PB_SLACK_BOT_TOKEN + PB_SLACK_APP_TOKEN "
                f"or store secrets '{SLACK_BOT_SECRET_KEY}' and '{SLACK_APP_SECRET_KEY}'."
            )
        return bot, app

    async def start(self) -> None:
        if self._started:
            return
        if self._owns_client and self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        self._bot_token, self._app_token = await self._resolve_tokens()
        self._started = True
        self._task = asyncio.create_task(self._run_loop(), name="pboy-slack-socket")
        logger.info("channel.connect name=slack transport=socket-mode")

    async def stop(self) -> None:
        if not self._started and self._task is None and not self._emit_tasks:
            return
        self._started = False
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._drain_emit_tasks()
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()
        logger.info("channel.disconnect name=slack")

    async def _drain_emit_tasks(self) -> None:
        pending = list(self._emit_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._emit_tasks.clear()

    async def _open_socket_url(self) -> str:
        assert self._app_token is not None
        res = await self._client.post(
            f"{self._api_base}/apps.connections.open",
            headers={"Authorization": f"Bearer {self._app_token}"},
        )
        res.raise_for_status()
        body = res.json()
        if not isinstance(body, dict) or not body.get("ok"):
            raise RuntimeError(f"apps.connections.open failed: {body!r}")
        url = body.get("url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("apps.connections.open returned no url")
        return url

    def _schedule_emit(self, event: dict[str, Any], *, dispatch: bool) -> None:
        task = asyncio.create_task(
            self._emit_safe({"event": event}, dispatch=dispatch),
            name="pboy-slack-emit",
        )
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)

    async def _emit_safe(self, payload: dict[str, Any], *, dispatch: bool) -> None:
        try:
            await self._emit(payload, "slack", dispatch=dispatch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("channel.slack emit_failed")

    async def handle_envelope(
        self,
        data: dict[str, Any],
        *,
        dispatch: bool = True,
        background: bool = False,
    ) -> Any | None:
        """Process one Socket Mode envelope (public for tests).

        Returns ``SlackChannel.RECONNECT`` when Slack requests a connection rollover.
        When ``background`` is True, ACK returns immediately and ``_emit`` runs as a task
        so the socket read loop is not blocked by long hook execution.
        """
        msg_type = data.get("type")
        if msg_type == "hello":
            return None
        if msg_type == "disconnect":
            reason = data.get("reason")
            logger.info("channel.slack disconnect reason={}", reason)
            return self.RECONNECT

        envelope_id = data.get("envelope_id")
        if isinstance(envelope_id, str) and self._ws is not None:
            await self._ws.send(json.dumps({"envelope_id": envelope_id}))

        if msg_type != "events_api":
            return None

        payload = data.get("payload")
        if not isinstance(payload, dict):
            return None
        event = payload.get("event")
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")
        # Some fixtures omit type; treat text+channel as a message.
        if event_type not in (None, "message"):
            return None
        # Ignore bot/system subtypes to avoid feedback loops.
        if event.get("subtype") or event.get("bot_id"):
            logger.debug("channel.slack skip subtype/bot message")
            return None
        if not event.get("text") and not event.get("channel"):
            return None

        if background:
            self._schedule_emit(event, dispatch=dispatch)
            return None
        return await self._emit({"event": event}, "slack", dispatch=dispatch)

    async def _run_loop(self) -> None:
        backoff = self._initial_backoff
        while self._started:
            try:
                url = await self._open_socket_url()
                async with websockets.connect(url, open_timeout=30, max_queue=32) as ws:
                    self._ws = ws
                    backoff = self._initial_backoff
                    logger.info("channel.slack socket connected")
                    async for raw in ws:
                        if not self._started:
                            break
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("channel.slack non-json frame")
                            continue
                        if not isinstance(data, dict):
                            continue
                        try:
                            result = await self.handle_envelope(data, background=True)
                        except Exception:
                            logger.exception("channel.slack envelope_failed")
                            continue
                        if result is self.RECONNECT:
                            # Graceful rollover: leave the inner loop and open a new URL.
                            break
                self._ws = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._ws = None
                logger.warning(
                    "channel.slack socket_error err={} backoff={:.1f}s",
                    redact_secret(str(exc), self._app_token),
                    backoff,
                )
                if not self._started:
                    break
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2.0, self._max_backoff)


def build_slack_channel(
    emit: EventEmitter,
    store: StoreBase,
    settings: Settings,
) -> SlackChannel:
    return SlackChannel(emit, store, settings)


__all__ = [
    "SLACK_API_BASE",
    "SLACK_APP_SECRET_KEY",
    "SLACK_BOT_SECRET_KEY",
    "SlackChannel",
    "build_slack_channel",
]
