# Pageboy (`pboy` / `pb`)

A Zero-Ops event orchestrator that accepts Telegram/Slack **wake-ups** locally and runs matching hooks (`exec`). (**Alpha · [PyPI](https://pypi.org/project/pboy/)**)

Jira/Confluence are not daemon webhooks — hook programs talk to them over REST. → [`docs/architecture-channels.md`](docs/architecture-channels.md)

## Install · run

```bash
uvx pboy ui
# or: uvx pboy@0.1.0 ui
```

Browser: [http://127.0.0.1:8080/ui](http://127.0.0.1:8080/ui)

No login — API and UI are reachable only from this machine (loopback).

| Path | Role |
|------|------|
| `/ui` | Hooks · events · executions |
| `/ui/settings` | Telegram / Slack tokens · channel on/off · Telegram allowlist pairing |

Default data locations: DB `~/.local/share/pboy/pboy.db` · encryption key `~/.config/pboy/secret.key`

## First scenario (Telegram)

1. Create a bot token with [@BotFather](https://t.me/BotFather).
2. In `/ui/settings`: paste the Telegram token → save → **Enable** → **pair your chat** with the 6-digit code (or fill the Allowlist field). Prefer this UI path over env vars.
   An empty allowlist allows **any** chat that messages the bot — pair before attaching sensitive hooks.
3. Register a hook (second shell while the daemon is up — CLI auto-connects to `http://127.0.0.1:8080`):

```bash
uvx pboy hook register \
  --name tg-echo \
  --topic 'telegram:message' \
  --exec 'echo hello-from-pboy' \
  --timeout 30
```

On Windows, if `echo` fails: `--exec 'cmd /c echo hello-from-pboy'`

4. Message the bot in Telegram → `/ui` shows the event and execution.
5. (Optional) Reply via the bot:

```bash
uvx pboy event complete <event_id> --summary 'Resolved' --sync telegram
```

Slack needs both a **bot token (`xoxb-`)** and an **app token (`xapp-`, `connections:write`)** for Socket Mode. Full steps: [`src/pboy/channels/README.md`](src/pboy/channels/README.md)

## Operator guide

Deploy/restart, happy-path checklist, channel/hook/`--daemon-url` split-brain and 403 troubleshooting:
[`docs/operator.md`](docs/operator.md)

## Security (short)

Pageboy is **local-only**. It binds loopback only; every request must come from the same machine with a local `Host`. LAN / `0.0.0.0` are not supported. **Do not put a reverse proxy in front** (rewriting `Host` to `localhost` makes remote clients look local). Other local users/processes on the same machine can also reach the daemon.

Production checklist · Telegram SSRF policy (fixed `api.telegram.org`, no user-controlled URLs) · inbound allowlist · local API/execution disclosure · known limits:
[`docs/security-contract.md`](docs/security-contract.md)

Layman path needs **no environment variables** — configure channels in `/ui/settings`.  
CLI / `PB_*` overrides (split-brain, tokens, paths): [`docs/operator.md`](docs/operator.md#environment-variables-pb-advanced).

## Common commands

```bash
uvx pboy --help
uvx pboy channel list
uvx pboy hook register --name ... --topic 'telegram:message' --exec '...' --timeout 30
uvx pboy event list
uvx pboy event get <id>            # omit execution stdout/stderr
uvx pboy event get <id> --verbose  # include streams (local trust)
uvx pboy event complete <id> --summary '...' --sync telegram
```

## Developers

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

Ops / `PB_*` env: [`docs/operator.md`](docs/operator.md) · Security: [`docs/security-contract.md`](docs/security-contract.md)  
Release: [`docs/releasing.md`](docs/releasing.md) · skill `release patch|minor|major`  
Roadmap: [`ROADMAP.md`](ROADMAP.md) · [Issues](https://github.com/fritzprix/pboy/issues)  
UI / channels: [`src/pboy/ui/README.md`](src/pboy/ui/README.md) · [`src/pboy/channels/README.md`](src/pboy/channels/README.md)

### Filters

Supported: `.path.to.field == "value"` / `!=`  
Other expressions are fail-closed (never match).

## HTTP API (direct calls)

OpenAPI is available while the daemon is running at `http://127.0.0.1:8080/docs` (`/openapi.json`).

### 1. Event inject (`POST /v1/events`) — expected `payload` per `source`

`POST /v1/events` normalizes platform fields into CloudEvent `data` (`summary`, `description`, `channel_id`, `entity_key`, `actor`, …) via `EventNormalizer`, based on `source` (`telegram` · `slack` · `jira` · `confluence` · `generic`). A `payload` shape that does not match `source` yields empty `data.summary` / `description`.

- **Telegram** (`source: "telegram"`, topic `telegram:message`):
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
- **Slack** (`source: "slack"`, topic `slack:message`):
  ```json
  {
    "source": "slack",
    "payload": {
      "event": {
        "client_msg_id": "msg-1",
        "channel": "C01234567",
        "thread_ts": "1700000000.000100",
        "user": "U01234567",
        "text": "deploy status?"
      }
    }
  }
  ```
- **Jira** (`source: "jira"`, topics `jira:issue.created` / `jira:issue.updated`):
  ```json
  {
    "source": "jira",
    "payload": {
      "webhookEvent": "jira:issue_created",
      "issue": {
        "id": "10001",
        "key": "PROJ-123",
        "fields": {
          "summary": "Login page 500 error",
          "description": "Steps to reproduce...",
          "priority": {"name": "High"},
          "project": {"key": "PROJ"},
          "creator": {"accountId": "acc-1", "displayName": "Alice"},
          "labels": ["bug"]
        }
      }
    }
  }
  ```
- **Confluence** (`source: "confluence"`, topic `confluence:page.updated`):
  ```json
  {
    "source": "confluence",
    "payload": {
      "page": {
        "id": "98765",
        "title": "Runbook",
        "body": {"storage": {"value": "<p>Updated content</p>"}},
        "version": {"number": 2}
      },
      "space": {"key": "ENG"},
      "user": {"accountId": "acc-1", "displayName": "Alice"}
    }
  }
  ```
- **Generic** (`source: "generic"`): `{"type": "com.custom.event", "data": {...}}` (or an arbitrary JSON object stored as `data`).

### 2. Event complete (`POST /v1/events/{event_id}/complete`) — `sync` type

- The CLI (`pboy event complete <id> --sync telegram`) accepts a comma-separated string, but the **HTTP API `sync` field is a string array (`list[str]`)** (e.g. `["telegram"]`).

```bash
curl -X POST http://127.0.0.1:8080/v1/events/<event_id>/complete \
  -H 'Content-Type: application/json' \
  -d '{
    "summary": "Resolved",
    "sync": ["telegram"]
  }'
```

## License

Apache-2.0
