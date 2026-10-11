# Channels — `pboy.channels`

Inbound platform receivers. Adapters push **raw** payloads into `Engine.process_event`
(normalization stays in `pboy.core.normalizer`).

## Module layout

| File | Role |
|------|------|
| `base.py` | `ChannelAdapter` / `EventEmitter` protocols |
| `registry.py` | Enable/start/stop via `PB_CHANNELS_ENABLED` |
| `fake.py` | In-process fake adapter (tests / demos) |
| `telegram.py` | Bot API long-poll (`getUpdates`) |
| *(planned)* `slack.py` | Socket Mode (M4) |

## Enable channels

**Preferred (persisted store + CLI):**

```bash
pboy channel set-secret telegram --value '123456:ABC...'
pboy channel enable telegram
pboy channel list
# • telegram [enabled, stopped, secret=yes]   # until daemon starts it

export PB_AUTH_TOKEN=dev-secret
pboy daemon --host 127.0.0.1 --port 8080
# /healthz → channels.running includes "telegram"
```

With `PB_DAEMON_URL` set, the same `pboy channel …` commands hit the daemon HTTP API
(and can start/stop adapters live).

**Env override** (wins over store when the variable is set, including empty):

```bash
export PB_CHANNELS_ENABLED=telegram
export PB_TELEGRAM_BOT_TOKEN='123456:ABC...'
```

## Telegram setup (BotFather)

1. In Telegram, talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Store the token and enable the channel:

```bash
pboy channel set-secret telegram --value '<token from BotFather>'
pboy channel enable telegram
```

Or use env: `PB_TELEGRAM_BOT_TOKEN` (overrides store when set).

3. Start the daemon (same DB as the CLI):

```bash
export PB_AUTH_TOKEN=dev-secret
pboy daemon --host 127.0.0.1 --port 8080
```

4. Register a hook for `telegram:message`, then message the bot:

```bash
export PB_DAEMON_URL=http://127.0.0.1:8080
pboy hook register \
  --name tg-echo \
  --topic 'telegram:message' \
  --exec 'echo hello-from-tg' \
  --timeout 30
pboy channel list
```

Long-poll calls `deleteWebhook` on start so it does not fight a webhook subscription.
On API errors the poller backs off exponentially (1s → 60s).

## Outbound sync (completion reply)

When a harness finishes work it can close the loop:

```bash
pboy event complete "$PB_EVENT_ID" \
  --summary 'Restarted worker-2' \
  --sync telegram
```

This uses the same bot token, posts to `event.data.channel_id`, and replies to
`event.data.entity_key` (message id) when numeric. Failed sync exits CLI non-zero
but the completion record is still persisted. Re-running
`pboy event complete … --sync telegram` retries **failed/missing** sync targets
only (successful ones are not re-sent; original summary is kept).

## Adapter contract

```python
class ChannelAdapter(Protocol):
    @property
    def name(self) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

## Planned transports

| Channel | Transport | Tunnel | Config |
|---------|-----------|--------|--------|
| Telegram | Long-poll `getUpdates` | No | Bot token |
| Slack | Socket Mode | No | App + bot token |
| Jira / Confluence | Webhook → relay | No | Relay URL |
