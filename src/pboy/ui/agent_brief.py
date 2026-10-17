"""Versioned English agent brief for the dashboard copy-to-clipboard control.

``AGENT_BRIEF`` holds a ``{base_url}`` placeholder; serve it via ``render_agent_brief``
so the brief names the address the daemon is actually reachable at.
"""

from __future__ import annotations

AGENT_BRIEF: str = """\
# Pageboy agent brief

You are helping an operator set up and use Pageboy (`pboy` / `pb`) on this machine.
Pageboy is a **local-only** event orchestrator: chat messages become events; matching
hooks run commands on this PC; completing an event can reply in chat.

The daemon for this brief is at `{base_url}`. If `pboy` is not on PATH, run every
command below as `uvx pboy …` instead (and write the skill that way too).

## Reply in chat (read this first)

Inbound messages create events. **To reply, complete the event with sync** — there
is no separate “send message” API.

```bash
pboy event reply <event_id> --summary 'your reply text'
# picks --sync telegram or --sync slack from the event source
# inside a hook (PB_EVENT_ID is set):
pboy event reply --summary 'your reply text'
```

| Source | How to reply | Notes |
|--------|--------------|-------|
| Telegram bot | `event reply` / `telegram reply` / `complete --sync telegram` | BotFather token; files via `--file-path` / `--file-id` |
| Slack | `event reply` / `complete --sync slack` | Bot token with `chat:write`; posts in-thread; text only (`--file-*` is rejected) |
| `pboy telegram chats|messages|…` | **Not for replies** | App API user-account read/export only |

`pboy telegram --help` lists **`reply`** next to chats/messages — that command is
**bot-token outbound via the event**, not App API. Do not conclude “telegram is
read-only” from chats/messages/download/export alone.

## Your three jobs (do them in order)

1. **Support configuration** — get channels and secrets into a working state via
   `/ui/settings` (or the CLI). Secrets are **store-only** (encrypted local DB).
   Do **not** set `PB_TELEGRAM_*` / `PB_SLACK_*` / `PB_CHANNELS_ENABLED`; those env
   overrides were removed and the daemon logs an error at startup if they are set.
2. **Create a session skill** — add a Cursor Agent Skill under
   `.cursor/skills/pboy-<short-purpose>/SKILL.md` that teaches how to operate Pageboy
   in **this** workspace (daemon URL/port, which channels are in use, hook names,
   reply/`event reply` conventions). Keep secrets out of the skill file.
3. **Subscribe to events** — register hooks with `pboy hook register` for the right
   topics (`telegram:message`, `slack:message`, …) and a **safe** `--exec` (no
   tokens/passwords in the command string). Confirm matches in `/ui` or
   `pboy event list` / `pboy hook list`.

## Local daemon

- Dashboard/API: `{base_url}` (loopback only; no login). Interactive API docs:
  `{base_url}/docs`.
- Start UI+daemon: `uvx pboy ui` or `pboy daemon --ui`.
- CLI auto-connects to a running daemon on the default port (8080). On any other
  port, pass `--daemon-url {base_url}`.
- Do not reverse-proxy the daemon or bind non-loopback hosts.

## Telegram: two separate rails (do not conflate)

| Rail | Purpose | Credentials |
|------|---------|-------------|
| **Bot** | Messages to the bot → hooks (`telegram:message`); replies via `event reply` | BotFather token + enable + pair/allowlist |
| **App API** | List/export chats the **user account** is in (`pboy telegram …`) | `api_id` / `api_hash` + one-time phone/code connect |

Bot token alone cannot list personal chats. App API does **not** trigger hooks and
does **not** send bot replies.

Until a chat is paired (or an allowlist is saved), **any** Telegram chat can trigger hooks.

## Configuration checklist

- Open `{base_url}/ui/settings`.
- Telegram bot: save `bot_token`, enable channel, pair chat (or set allowlist).
- Optional App API: save `api_id` / `api_hash`, complete account connect; use
  `pboy telegram chats|messages|download|export`.
- Slack: save `bot_token` (`xoxb-…`, needs the `chat:write` scope to reply) and
  `app_token` (`xapp-…` with `connections:write`), enable Socket Mode on the Slack
  app, enable channel.
- CLI alternatives: `pboy channel set-secret`, `pboy channel enable`,
  `pboy channel list`.

## Hooks and completion

```bash
pboy hook register \\
  --name <name> \\
  --topic 'telegram:message' \\
  --exec '<safe command>' \\
  --timeout 30

pboy hook list
pboy event list
pboy event get <id> --verbose
pboy event reply <id> --summary '…'
```

- Topics include: `telegram:message`, `slack:message`, `jira:issue.created`,
  `jira:issue.updated`, `confluence:page.updated`.
- `--filter` is fail-closed: only `.path == "value"` / `!=` are supported; anything
  else matches nothing.
- Hook process gets event JSON on stdin and env such as `PB_EVENT_ID`, `PB_SOURCE`,
  `PB_HOOK_NAME`. Reply with `pboy event reply` and `$PB_EVENT_ID`; it picks
  Telegram or Slack from the event source (`complete --sync telegram|slack` is the
  explicit form).
- Never put secrets in `hook.exec`. Prefer allowlisted chats over open inbound.

## Docs

- https://github.com/fritzprix/pboy#readme — event → hook → reply flow, writing hooks
- https://github.com/fritzprix/pboy/blob/main/docs/operator.md — operator runbook and troubleshooting
- https://github.com/fritzprix/pboy/blob/main/docs/security-contract.md — loopback / local-only contract
- https://github.com/fritzprix/pboy/blob/main/src/pboy/channels/README.md — channel setup detail

## Skill file sketch

Create `.cursor/skills/pboy-<purpose>/SKILL.md` with YAML front matter
(`name`, `description`) and sections: when to use, daemon/CLI assumptions for this
machine, channel rails in use, how to register/list hooks, how to `event reply` /
`complete --sync telegram|slack`, and safety (store-only secrets, no exec secrets,
allowlist). Point the operator to `/ui` and the docs above rather than inventing
remote access.
"""


def render_agent_brief(base_url: str) -> str:
    """Return the brief with ``{base_url}`` filled in (no trailing slash)."""
    return AGENT_BRIEF.replace("{base_url}", base_url.rstrip("/"))


__all__ = ["AGENT_BRIEF", "render_agent_brief"]
