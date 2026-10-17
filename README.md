# Pageboy (`pboy` / `pb`)

[![PyPI](https://img.shields.io/pypi/v/pboy.svg)](https://pypi.org/project/pboy/)
[![Python](https://img.shields.io/pypi/pyversions/pboy.svg)](https://pypi.org/project/pboy/)
[![License](https://img.shields.io/pypi/l/pboy.svg)](https://github.com/fritzprix/pboy/blob/main/LICENSE)
[![CI](https://github.com/fritzprix/pboy/actions/workflows/ci.yml/badge.svg)](https://github.com/fritzprix/pboy/actions/workflows/ci.yml)

Send a message to your Telegram or Slack bot, and a script runs **on your own machine**.
Pageboy is a local-first event orchestrator: it receives chat messages without a public
webhook URL (Telegram long-poll, Slack Socket Mode), turns them into events, and runs the
hooks you registered for them. The daemon and its dashboard listen on loopback only.

Status: alpha.

## How it works

```text
chat message ──▶ event ──▶ matching hook runs ──▶ event reply / complete --sync ──▶ reply in chat
 (Telegram/Slack)  (CloudEvent)  (your command,        (CLI or HTTP)     (telegram|slack)
                                  event JSON on stdin)
```

- An **event** is a CloudEvents-shaped record of something that happened, e.g. a Telegram message.
- A **hook** is a command you register for a **topic** such as `telegram:message`.
  When a matching event arrives, Pageboy spawns the command and streams the event to it.
- **Completing** an event records the outcome and can optionally post a reply back to the chat.

## Quick start

```bash
uvx pboy ui            # starts the daemon + dashboard (pin a version: uvx pboy@0.2.0 ui)
```

Open <http://127.0.0.1:8080/ui>. There is no login; it only accepts local connections.
Leave this running and use a second terminal for the CLI. The CLI finds the running daemon automatically.

## First scenario: Telegram echo

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. In <http://127.0.0.1:8080/ui/settings>, save the token, press **Enable**, then **pair your chat**.
   > Until you pair a chat (or set an allowlist), **any** Telegram chat can trigger your hooks.
3. Register a hook:

   ```bash
   uvx pboy hook register \
     --name tg-echo \
     --topic 'telegram:message' \
     --exec 'echo hello-from-pboy' \
     --timeout 30
   ```

   On Windows, use `--exec 'cmd /c echo hello-from-pboy'`.
4. Message the bot. The event and the hook execution appear in `/ui`.
5. Reply to the chat (bot token — **not** `pboy telegram …`):

   ```bash
   uvx pboy event reply <event_id> --summary 'Resolved'
   # same as: uvx pboy event complete <event_id> --summary 'Resolved' --sync telegram
   ```

## Writing hooks

A hook command receives:

| Input | Contents |
|-------|----------|
| stdin | The event as JSON (see below) |
| `PB_EVENT_ID` | Event id; `pboy event reply` / `complete` use it by default |
| `PB_SOURCE` | `telegram`, `slack`, … |
| `PB_HOOK_NAME` | Name of the hook being run |
| `PB_LEASE_TOKEN` | Lease token, when the event is leased |

A Telegram message event looks like this (abridged):

```json
{
  "id": "evt_20261017_2a51bd08c4a9",
  "source": "telegram",
  "type": "com.telegram.message.received",
  "data": {
    "channel_id": "123456789",
    "actor": {"id": "987654321", "display_name": "alice"},
    "summary": "hello"
  }
}
```

So a hook can read the message, do its work, and reply in one script:

```bash
#!/usr/bin/env bash
text=$(jq -r '.data.summary')
result=$(do-something-with "$text")
pboy event reply --summary "$result"   # uses $PB_EVENT_ID; bot reply
```

**Topics:** `telegram:message`, `slack:message`, `jira:issue.created`, `jira:issue.updated`,
`confluence:page.updated`.

**Filters** narrow a topic further. Only `==` and `!=` on a field path are supported;
any other expression matches nothing (it fails closed):

```bash
uvx pboy hook register --name ping --topic 'telegram:message' \
  --filter '.data.summary == "ping"' --exec './pong.sh'
```

## Channels

Configure every channel in `/ui/settings`. Secrets are stored encrypted in the local database,
and there are no env-var overrides for tokens.

### Telegram: two separate connections

| | Bot token | App API (user account) |
|---|---|---|
| Purpose | Messages to your bot trigger hooks | Read and export chats your own account is in |
| Credentials | Token from @BotFather | `api_id` / `api_hash` from [my.telegram.org](https://my.telegram.org), plus a one-time account connect (phone + code) |
| Used by | The daemon and hooks | `pboy telegram …` commands |
| Reply / send | `pboy telegram reply` / `event reply` / `complete --sync telegram` | **No** — chats/messages/export only |

The bot token alone cannot list your chats; the App API does not trigger hooks and
does not send bot replies.

```bash
uvx pboy telegram chats
uvx pboy telegram messages <chat> --limit 100
uvx pboy telegram download <chat> <message_id>
uvx pboy telegram export <chat> --out chat.jsonl --format jsonl   # or md, csv
```

### Slack

Socket Mode needs a bot token (`xoxb-…`) and an app-level token (`xapp-…` with
`connections:write`). Save both in `/ui/settings` and enable the channel.

Full setup for both channels: [`src/pboy/channels/README.md`](src/pboy/channels/README.md).

## CLI reference

```bash
uvx pboy --help

# hooks
uvx pboy hook register --name … --topic … --exec … [--filter …] [--timeout 30]
uvx pboy hook list
uvx pboy hook delete <name>

# events
uvx pboy event list
uvx pboy event get <id>                # without execution output
uvx pboy event get <id> --verbose      # include hook stdout/stderr
uvx pboy event reply <id> --summary '…'          # auto telegram|slack from event
uvx pboy event complete <id> --summary '…' --sync telegram
uvx pboy event complete <id> --summary '…' --sync slack

# channels
uvx pboy channel list
uvx pboy channel enable telegram
```

If the daemon runs on a non-default port, pass `--daemon-url http://127.0.0.1:<port>`.

## HTTP API

While the daemon is running, interactive docs are at <http://127.0.0.1:8080/docs>
(schema: `/openapi.json`).

Inject an event (Telegram-shaped payload):

```bash
curl -X POST http://127.0.0.1:8080/v1/events \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "telegram",
    "payload": {
      "message": {
        "message_id": 42,
        "chat": {"id": 123456789},
        "from": {"id": 987654321, "username": "alice"},
        "text": "hello from curl"
      }
    }
  }'
```

Complete an event. Over HTTP, `sync` is a JSON array; the CLI's `--sync` is comma-separated:

```bash
curl -X POST http://127.0.0.1:8080/v1/events/<event_id>/complete \
  -H 'Content-Type: application/json' \
  -d '{"summary": "Resolved", "sync": ["telegram"]}'
```

Payload shapes for Slack, Jira, Confluence and generic events: see the OpenAPI examples and
[`docs/operator.md`](docs/operator.md).

## Security

The daemon binds to loopback and only accepts a local `Host`; do not put it behind a reverse proxy.
**Any process on this machine that can reach the port has full API access**, and that includes
registering hooks, which means running commands.

Checklist, allowlist guidance, SSRF policy and App API session risks:
[`docs/security-contract.md`](docs/security-contract.md).

## Configuration and data

- Channel secrets and enablement: `/ui/settings` (or `pboy channel set-secret` / `enable`).
- Database: `~/.local/share/pboy/pboy.db`
- Encryption key: `~/.config/pboy/secret.key`
- Advanced `PB_*` environment knobs: [`docs/operator.md`](docs/operator.md#environment-variables-pb_-advanced).

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/operator.md`](docs/operator.md) | Deploy, happy path, troubleshooting |
| [`docs/security-contract.md`](docs/security-contract.md) | Threat model and checklist |
| [`docs/architecture-channels.md`](docs/architecture-channels.md) | Wake-up channels vs Jira/Confluence agent REST |
| [`src/pboy/channels/README.md`](src/pboy/channels/README.md) | Telegram / Slack in detail |
| [`src/pboy/ui/README.md`](src/pboy/ui/README.md) | Dashboard notes |
| [`docs/releasing.md`](docs/releasing.md) | Release process (maintainers) |
| [`ROADMAP.md`](ROADMAP.md) · [Issues](https://github.com/fritzprix/pboy/issues) | Plans and bug reports |

## Development

```bash
git clone https://github.com/fritzprix/pboy.git
cd pboy
uv sync --extra dev
uv run pboy ui

uv run pytest -q
uv run ruff check src tests
uv run mypy src
./scripts/smoke_demo.sh
```

## License

Apache-2.0
