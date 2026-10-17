# Channels — `pboy.channels`

Inbound **wake-up** receivers. Adapters push **raw** payloads into `Engine.process_event`
(normalization stays in `pboy.core.normalizer`).

Ticketing systems (Jira / Confluence) are **not** required ChannelAdapters: agents
call their REST APIs from the harness. See [`docs/architecture-channels.md`](../../../docs/architecture-channels.md).

## Module layout

| File | Role |
|------|------|
| `base.py` | `ChannelAdapter` / `EventEmitter` protocols |
| `registry.py` | Enable/start/stop via encrypted store |
| `telegram_app.py` | MTProto App API (dialogs / export; user account) |
| `fake.py` | In-process fake adapter (tests / demos) |
| `telegram.py` | Bot API long-poll (`getUpdates`) |
| `slack.py` | Socket Mode (Events API over WebSocket) |

## Enable channels

**Preferred (persisted store + CLI):**

```bash
pboy channel set-secret telegram --value '123456:ABC...'
pboy channel enable telegram
pboy channel list
# • telegram [enabled, stopped, secret=yes]   # until daemon starts it

pboy daemon --host 127.0.0.1 --port 8080
# /healthz → channels.running includes "telegram"
```

While the daemon is running on `127.0.0.1:8080`, `pboy channel …` commands automatically connect to the daemon HTTP API (and can start/stop adapters live). For custom ports, pass `--daemon-url http://127.0.0.1:<port>`.

Channel secrets and enablement are **store-only** (`/ui/settings` or `pboy channel set-secret` / `enable`). Removed env overrides (`PB_TELEGRAM_*` tokens/allowlist, `PB_SLACK_*`, `PB_CHANNELS_ENABLED`) log a loud error at daemon startup if still set.

## Telegram setup (BotFather)

1. In Telegram, talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Store the token and enable the channel:

```bash
pboy channel set-secret telegram --value '<token from BotFather>'
pboy channel enable telegram
```

Prefer `/ui/settings` (store-only secrets — no `PB_TELEGRAM_*` token overrides).

Optional **allowlist** (comma-separated `chat.id` or sender `from.id` — unset/empty allows all; when set, unauthorized updates are dropped before file download or event creation).
Note: allowing a group `chat.id` permits **all members** in that chat; list individual `from.id` values if you only want specific senders. Pass `--value ''` (or save an empty field in `/ui/settings`) to clear the stored allowlist:

```bash
pboy channel set-secret telegram --secret-name allowlist --value '123456789,-100987654321'
# clear allowlist (revert to allow all):
pboy channel set-secret telegram --secret-name allowlist --value ''
```

### App API rail (dialogs / export — not wake-up)

Separate from the bot: save `api_id` / `api_hash` (my.telegram.org) and complete
**account connect** in `/ui/settings`, then:

```bash
pboy telegram chats
pboy telegram messages <chat> --limit 100 --offset-id 0
pboy telegram download <chat> <message_id>
pboy telegram export <chat> --out chat.jsonl --format jsonl
```

Bot token alone cannot list the user's full dialog set.

3. Start the daemon (same DB as the CLI):

```bash
pboy daemon --host 127.0.0.1 --port 8080
```

4. Register a hook for `telegram:message`, then message the bot:

```bash
pboy hook register \
  --name tg-echo \
  --topic 'telegram:message' \
  --exec 'echo hello-from-tg' \
  --timeout 30
pboy channel list
```

Long-poll calls `deleteWebhook` on start so it does not fight a webhook subscription.
On API errors the poller backs off exponentially (1s → 60s).

## Inbound files (Telegram)

Photo / document / audio / video messages populate `event.data.attachments`
(with `file_id`, `file_type`, size, …). Caption (or text) becomes `summary`.

When `PB_TELEGRAM_DOWNLOAD_FILES` is true (default), the channel downloads via
`getFile` into `~/.local/share/pboy/files/telegram/<update_id>/` (capped ~20 MB)
and sets `local_path`. Download failures still emit metadata without `local_path`.
Poll dispatch runs in the background so long hooks do not stall `getUpdates`.

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

To send a **reply file** (explicit — never auto-echoes inbound attachments):

```bash
pboy event complete "$PB_EVENT_ID" \
  --summary 'Report attached' \
  --sync telegram \
  --file-path ./out.png \
  --file-type photo
# or: --file-id '<telegram-file-id>' --file-type document
```

The CLI resolves/stages ``--file-path`` under ``data_dir/files`` (copying into
``files/outbound/<uuid>_name`` when needed) so the daemon path guard cannot read
arbitrary host files. Direct ``POST /v1/events/.../complete`` with ``file_path``
is likewise restricted to that directory.

Re-running ``event complete --sync telegram --file-path …`` after a successful
text sync **sends the original summary again as the media caption** (summary is
not replaced on retry). That is intentional.

## Adapter contract

```python
class ChannelAdapter(Protocol):
    @property
    def name(self) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

## Transports

| Channel | Transport | Tunnel | Role |
|---------|-----------|--------|------|
| Telegram | Long-poll `getUpdates` | No | Wake-up ✅ |
| Slack | Socket Mode | No | Wake-up ✅ |
| Jira / Confluence | **Agent REST** (GET/POST) | No | Work surface — not a ChannelAdapter |
| Jira webhook + relay | Push HTTPS | Public URL | Deferred / optional only |

## Slack setup (Socket Mode)

1. Create a Slack app with **Socket Mode** enabled; generate an **App-level token**
   (`xapp-…`) with `connections:write`, and a **Bot token** (`xoxb-…`) with
   `chat:write` + event subscriptions for `message.channels` / `message.im` as needed.
2. Store secrets (both required):

```bash
pboy channel set-secret slack --secret-name bot_token --value 'xoxb-...'
pboy channel set-secret slack --secret-name app_token --value 'xapp-...'
pboy channel enable slack
```

Prefer `/ui/settings` for both Slack tokens (store-only).

3. Start daemon, register a hook on `slack:message`, then message the app.
