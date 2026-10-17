# Operator guide

Run Pageboy against one wake-up channel **without reading the source**.

Alpha threat model: **local-only** daemon (loopback + `LocalOnlyGuard`). There is no
login and no `PB_AUTH_TOKEN`. Details: [`security-contract.md`](security-contract.md).

## Happy-path checklist (Telegram)

Do this once end-to-end before debugging anything else.

1. **Start UI + daemon** (one process):

   ```bash
   uvx pboy ui
   # equivalent: uvx pboy daemon --ui --host 127.0.0.1 --port 8080
   ```

   Open [http://127.0.0.1:8080/ui](http://127.0.0.1:8080/ui).

2. **Bot rail (wake-up)** in `/ui/settings`: paste BotFather token → save → **Enable** →
   pair your chat (or set **Allowlist**) so only authorized chats can trigger hooks.
   Secrets are store-only (no `PB_TELEGRAM_*`).

3. **(Optional) App rail (dialogs / export)** — separate from the bot: save **App API ID/Hash**
   from [my.telegram.org](https://my.telegram.org) → **계정 연결** (phone + code, optional 2FA).
   Then:

   ```bash
   uvx pboy telegram chats
   uvx pboy telegram messages <chat> --limit 100
   uvx pboy telegram export <chat> --out chat.jsonl --format jsonl
   ```

4. **Register a hook** (second shell; auto-connects to the running daemon):

   ```bash
   uvx pboy hook register \
     --name tg-echo \
     --topic 'telegram:message' \
     --exec 'echo hello-from-pboy' \
     --timeout 30
   ```

5. **Message the bot** in Telegram → `/ui` shows event + execution.

6. **Complete + reply** (optional):

   ```bash
   uvx pboy event list
   uvx pboy event complete <event_id> --summary 'Resolved' --sync telegram
   ```

Slack: bot token (`xoxb-`) **and** app token (`xapp-`, `connections:write`), topic
`slack:message`. CLI needs **both** secrets with `--secret-name`:

```bash
uvx pboy channel set-secret slack --secret-name bot_token --value 'xoxb-...'
uvx pboy channel set-secret slack --secret-name app_token --value 'xapp-...'
```

Full steps: [`src/pboy/channels/README.md`](../src/pboy/channels/README.md).

---

## Deploy / runbook

### Start

| Command | What it does |
|---------|----------------|
| `uvx pboy ui` | Daemon + dashboard at `/ui` (recommended) |
| `uvx pboy daemon --ui` | Same, explicit flags |
| `uvx pboy daemon` | API only (`/healthz`, `/v1/*`) — no UI |

Defaults: `--host 127.0.0.1` `--port 8080`. Non-loopback bind is **refused**.

Pin a version: `uvx pboy@0.1.0 ui`.

### Data locations

| What | Default path |
|------|----------------|
| SQLite DB | `~/.local/share/pboy/pboy.db` |
| Secret key (mode `0600`) | `~/.config/pboy/secret.key` |
| Inbound Telegram files | `~/.local/share/pboy/files/telegram/` |
| Staged outbound files | `~/.local/share/pboy/files/outbound/` |
| Telegram App media | `~/.local/share/pboy/files/telegram_app/` |

**File retention:** staged files under `data_dir/files/` are **kept forever** by
default (no daemon startup sweep). Delete manually or prune by age:

```bash
uvx pboy files prune --dry-run --older-than 7d
uvx pboy files prune --older-than 7d
```

Override DB with `--db` / `PB_DATABASE_URL` if you need an isolated instance.

### CLI ↔ daemon connection (`--daemon-url` / `-u`)

When the daemon is running on the default address (`http://127.0.0.1:8080`), **the CLI automatically detects and connects to it** (via `/healthz`). When the daemon is stopped, the CLI falls back to the local SQLite DB (`~/.local/share/pboy/pboy.db`).

If you run the daemon on a custom port, pass `--daemon-url` (`-u`):

```bash
uvx pboy --daemon-url http://127.0.0.1:9000 status
```

If the daemon runs on a custom port or isolated DB (`--db`) and you run the CLI **without** `--daemon-url`, the CLI opens the default local SQLite DB instead of talking to that daemon (**split-brain**).

### Environment variables (PB_*, advanced)

**Layman / UI-only installs need none of these.** Channel tokens, allowlist, App API
keys, account connect, and channel enable live in `/ui/settings` (encrypted store) —
**not** in `PB_TELEGRAM_*` / `PB_SLACK_*` / `PB_CHANNELS_ENABLED` (removed).

| Variable | When to use | Notes |
|----------|-------------|-------|
| `PB_DATABASE_URL` / `--db` | Isolated DB | Default: `~/.local/share/pboy/pboy.db`. When set, CLI uses this local DB directly unless `--daemon-url` is passed. |
| `PB_SECRET_KEY` | Fixed key in tests/CI | Default: file at `~/.config/pboy/secret.key`. |
| `PB_CONFIG_DIR` / `PB_DATA_DIR` | Custom paths | Defaults under `~/.config/pboy` and `~/.local/share/pboy`. |
| `PB_HOST` / `PB_PORT` / `PB_LOG_LEVEL` | Bind / logging | Prefer `pboy ui` / `pboy daemon` flags when possible. |
| `PB_TELEGRAM_DOWNLOAD_FILES` | Toggle inbound Bot API file download | Non-secret ops knob (default true). |

Harness-injected (not operator config — set by the daemon for hook subprocesses):
`PB_EVENT_ID`, `PB_SOURCE`, `PB_HOOK_NAME`, and optionally `PB_LEASE_TOKEN`.
See [`security-contract.md`](security-contract.md).

### Survive reboot (optional systemd user unit)

Sketch only — adjust `ExecStart` to your install (`uvx` path or a venv):

```ini
# ~/.config/systemd/user/pboy.service
[Unit]
Description=Pageboy local daemon
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/uvx pboy ui --host 127.0.0.1 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now pboy.service
# linger so the unit survives logout (optional):
# loginctl enable-linger "$USER"
```

Do **not** publish the port, put a reverse proxy in front, or run on a shared
multi-user host you do not trust. See [`security-contract.md`](security-contract.md).

### Health

```bash
uvx pboy status
# or: curl -s http://127.0.0.1:8080/healthz
# channels.running should list enabled adapters (e.g. "telegram")
```

---

## Troubleshooting

### Channel not receiving

1. Daemon process still running? UI / `uvx pboy status` / `curl …/healthz`.
2. `uvx pboy channel list` (pass `--daemon-url` if using a non-default port) — `secret=yes`, `enabled`, and
   **running** (not only stopped-in-store).
3. Telegram: token valid; BotFather bot started with `/start`; long-poll calls
   `deleteWebhook` on start — a competing webhook elsewhere will steal updates.
4. Secrets must be in the store (`/ui/settings` or `pboy channel set-secret`) — channel
   env overrides were removed.
5. Slack: both `bot_token` and `app_token` (CLI: `--secret-name`); Socket Mode on.
6. Dialog list / export fails? That is the **App rail** — need api_id/hash + account
   connect in settings (bot token alone is not enough).

### Hook not matching / not running

1. Topic must match the normalized type, e.g. `telegram:message` or `slack:message`
   (typos → no match).
2. Hook `status` must be `active` (`pboy hook list`).
3. `--filter` is fail-closed: unsupported expressions never match. Supported:
   `.path.to.field == "value"` / `!=`.
4. Confirm the **event** landed (`pboy event list` / `/ui`) — if no event, fix the
   channel first.
5. Failed harness: `pboy event get <id> --verbose` shows stdout/stderr; spawn errors
   include full `hook.exec` and parsed `argv`.

### Split-brain (CLI vs daemon DB)

| Symptom | Cause |
|---------|--------|
| Hook registered but daemon never runs it | Daemon uses a custom port or `--db`, and CLI ran without matching `--daemon-url` |
| UI empty, CLI `event list` has rows | Same |
| Channel enabled in CLI, `/healthz` not running it | Enable hit local store while daemon was stopped (or on custom port without `--daemon-url`) |

Fix: run the daemon on the default port (`8080`) so CLI auto-connects, or pass `--daemon-url http://127.0.0.1:<port>` when using a custom port.

### HTTP 403 / cannot reach UI or API

There is **no** auth token and **no** HTTP 401 login flow.

| Response | Typical cause |
|----------|----------------|
| **403** | Non-loopback peer, bad `Host` (DNS rebinding / proxy), or browser `Sec-Fetch-Site: cross-site` / foreign `Origin` on mutating requests |
| Connection refused | Daemon not running or wrong port |
| Bind error at startup | Tried `--host 0.0.0.0` or a LAN address — only loopback is allowed |

Use `http://127.0.0.1:8080` (or `localhost`) from the **same machine**. Do not reverse-proxy.

### Auth / “token” confusion

Older docs mentioned `PB_AUTH_TOKEN` — **removed**. Local safety is bind +
`LocalOnlyGuard` only. Any local process that can open the loopback port can call the
API (including creating hooks → command execution as the daemon OS user).

---

## Related docs

| Doc | Audience |
|-----|----------|
| [`../README.md`](../README.md) | Quick start (UI-first; no env required) |
| [`security-contract.md`](security-contract.md) | Local-only threat model |
| [`architecture-channels.md`](architecture-channels.md) | Wake-up vs Jira agent REST |
| [`../src/pboy/channels/README.md`](../src/pboy/channels/README.md) | Telegram / Slack setup detail |
| [`releasing.md`](releasing.md) | Maintainers / PyPI |
| This guide § [Environment variables](#environment-variables-pb-advanced) | Full `PB_*` reference |
