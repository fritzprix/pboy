# Channels — `pboy.channels`

Inbound **wake-up** receivers: they turn a chat message into an event that can trigger hooks.
Adapters push **raw** payloads into `Engine.process_event`; normalization stays in
`pboy.core.normalizer`.

Ticketing systems (Jira / Confluence) are **not** ChannelAdapters: agents call their REST
APIs from the harness. See [`docs/architecture-channels.md`](../../../docs/architecture-channels.md).

| Channel | Transport | Public URL needed | Role |
|---------|-----------|-------------------|------|
| Telegram | Bot API long-poll `getUpdates` | No | Wake-up |
| Slack | Socket Mode (WebSocket) | No | Wake-up |
| Jira / Confluence | Agent REST (GET/POST) | No | Work surface — not a ChannelAdapter |
| Jira webhook + relay | Push HTTPS | Yes | Deferred / optional only |

## Configuring channels

Channel secrets and enablement are **store-only**: set them in `/ui/settings` (preferred) or
with the CLI. The removed env overrides (`PB_TELEGRAM_*` tokens/allowlist, `PB_SLACK_*`,
`PB_CHANNELS_ENABLED`) log a loud error at daemon startup if still set.

```bash
pboy channel set-secret <channel> [--secret-name <name>] --value '…'
pboy channel enable <channel>
pboy channel list
# • telegram [enabled, stopped, secret=yes]   # until the daemon starts it
```

While the daemon runs on `127.0.0.1:8080`, `pboy channel …` talks to its HTTP API and can
start/stop adapters live. For a custom port, pass `--daemon-url http://127.0.0.1:<port>`.

## Telegram

### Setup

1. In Telegram, talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Store the token and enable the channel:

   ```bash
   pboy channel set-secret telegram --value '<token from BotFather>'
   pboy channel enable telegram
   ```

3. Start the daemon (it uses the same DB as the CLI):

   ```bash
   pboy daemon --host 127.0.0.1 --port 8080
   # /healthz → channels.running includes "telegram"
   ```

4. Register a hook for `telegram:message`, then message the bot:

   ```bash
   pboy hook register \
     --name tg-echo \
     --topic 'telegram:message' \
     --exec 'echo hello-from-tg' \
     --timeout 30
   ```

Long-poll calls `deleteWebhook` on start so it does not fight a webhook subscription.
On API errors the poller backs off exponentially (1s → 60s).

### Allowlist

Comma-separated `chat.id` or sender `from.id` values. Unset or empty allows **all** chats;
when set, unauthorized updates are dropped before file download or event creation.
The **pair your chat** button in `/ui/settings` fills this in for you.

Allowing a group `chat.id` permits **all members** of that chat; list individual `from.id`
values if you only want specific senders.

```bash
pboy channel set-secret telegram --secret-name allowlist --value '123456789,-100987654321'
# clear (revert to allow all):
pboy channel set-secret telegram --secret-name allowlist --value ''
```

### Inbound files

Photo / document / audio / video messages populate `event.data.attachments`
(`file_id`, `file_type`, size, …). The caption (or text) becomes `summary`.

When `PB_TELEGRAM_DOWNLOAD_FILES` is true (default), the channel downloads via `getFile`
into `~/.local/share/pboy/files/telegram/<update_id>/` (capped at ~20 MB) and sets
`local_path`. Download failures still emit metadata, without `local_path`.
Poll dispatch runs in the background (max 4 concurrent download+emit; max 32 pending
tasks with backpressure on `getUpdates` when full), so long hooks do not stall polling
forever under burst load. Staged files are not auto-deleted — see
[`docs/operator.md`](../../../docs/operator.md) (`pboy files prune`).

### Replying (outbound sync)

When a hook finishes, close the loop with a **bot** reply (same BotFather token that
received the message). Prefer the discoverable shortcut:

```bash
pboy event reply --summary 'Restarted worker-2'   # uses $PB_EVENT_ID
# equivalent:
pboy event complete --summary 'Restarted worker-2' --sync telegram
```

Do **not** use `pboy telegram …` for this — that group is App API (user account)
read/export only and cannot send bot messages.

This uses the bot token, posts to `event.data.channel_id`, and replies to
`event.data.entity_key` (the message id) when numeric. A failed sync makes the CLI exit
non-zero, but the completion record is still saved. Re-running
`pboy event complete … --sync telegram` / `event reply` retries **failed/missing**
sync targets only (successful ones are not re-sent; the original summary is kept).

To send a **reply file** (explicit — inbound attachments are never echoed automatically):

```bash
pboy event reply --summary 'Report attached' \
  --file-path ./out.png \
  --file-type photo
# or: --file-id '<telegram-file-id>' --file-type document
```

The CLI stages `--file-path` under `data_dir/files` (copying into
`files/outbound/<uuid>_name` when needed), so the daemon path guard cannot read arbitrary
host files. A direct `POST /v1/events/.../complete` with `file_path` is restricted to the
same directory.

Re-running `event complete --sync telegram --file-path …` after a successful text sync
**sends the original summary again as the media caption** (the summary is not replaced on
retry). This is intentional.

## Telegram App API (user account)

A separate connection from the bot, implemented in `telegram_app.py` (MTProto). It reads
the chats **your own account** is in; it does not trigger hooks. The bot token alone cannot
list your dialogs.

1. Get `api_id` / `api_hash` from [my.telegram.org](https://my.telegram.org) → API development tools.
2. Save both in `/ui/settings`, then complete **account connect** (phone + code, plus the
   2FA password if your account has one). The session is stored encrypted; you can
   disconnect it from the same page.
3. Use the CLI:

   ```bash
   pboy telegram chats [--search <text>]
   pboy telegram messages <chat> --limit 100 --offset-id 0
   pboy telegram download <chat> <message_id>
   pboy telegram export <chat> --out chat.jsonl --format jsonl   # or md, csv
   ```

## Slack (Socket Mode)

1. Create a Slack app with **Socket Mode** enabled. Generate an **app-level token**
   (`xapp-…`) with `connections:write` and a **bot token** (`xoxb-…`) with `chat:write`,
   plus event subscriptions for `message.channels` / `message.im` as needed.
2. Store both tokens (both are required) and enable the channel:

   ```bash
   pboy channel set-secret slack --secret-name bot_token --value 'xoxb-...'
   pboy channel set-secret slack --secret-name app_token --value 'xapp-...'
   pboy channel enable slack
   ```

3. Start the daemon, register a hook on `slack:message`, then message the app.
4. Reply in the same thread:

   ```bash
   pboy event reply --summary 'Restarted worker-2'   # uses $PB_EVENT_ID; --sync slack
   # or: pboy event complete "$PB_EVENT_ID" --summary '…' --sync slack
   ```

   Uses the bot token (`chat.postMessage` with `thread_ts` from the inbound event).

## Module layout

| File | Role |
|------|------|
| `base.py` | `ChannelAdapter` / `EventEmitter` protocols |
| `registry.py` | Enable / start / stop adapters from the encrypted store |
| `config.py` | Secret keys per channel, enabled-list persistence |
| `env_contract.py` | Startup check for removed `PB_*` channel env vars |
| `telegram.py` | Bot API long-poll (`getUpdates`) |
| `telegram_files.py` | Inbound Telegram file download |
| `telegram_app.py` | MTProto App API (dialogs / export; user account) |
| `slack.py` | Socket Mode (Events API over WebSocket) |
| `fake.py` | In-process fake adapter (tests / demos) |

## Adapter contract

```python
class ChannelAdapter(Protocol):
    @property
    def name(self) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```
