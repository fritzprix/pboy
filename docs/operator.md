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

2. **Enable Telegram** in `/ui/settings`: paste BotFather token → save → **Enable**.  
   Or CLI (same machine, after setting `PB_DAEMON_URL` — see below):

   ```bash
   export PB_DAEMON_URL=http://127.0.0.1:8080
   uvx pboy channel set-secret telegram --value '<bot-token>'
   uvx pboy channel enable telegram
   uvx pboy channel list   # expect enabled + running while daemon is up
   ```

3. **Register a hook** (second shell; must use the daemon URL):

   ```bash
   export PB_DAEMON_URL=http://127.0.0.1:8080
   uvx pboy hook register \
     --name tg-echo \
     --topic 'telegram:message' \
     --exec 'echo hello-from-pboy' \
     --timeout 30
   ```

4. **Message the bot** in Telegram → `/ui` shows event + execution.

5. **Complete + reply** (optional):

   ```bash
   export PB_DAEMON_URL=http://127.0.0.1:8080
   uvx pboy event list
   uvx pboy event complete <event_id> --summary 'Resolved' --sync telegram
   ```

Slack: bot token (`xoxb-`) **and** app token (`xapp-`, `connections:write`), topic
`slack:message`. Step-by-step: [`src/pboy/channels/README.md`](../src/pboy/channels/README.md).

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

Override DB with `--db` / `PB_DATABASE_URL` if you need an isolated instance.

### CLI ↔ daemon (`PB_DAEMON_URL`)

While the daemon runs, **always** point the CLI at it:

```bash
export PB_DAEMON_URL=http://127.0.0.1:8080
```

If unset, CLI opens **its own** SQLite under the default data dir — hooks/events you
register there never reach the running daemon (**split-brain**).

Harness subprocesses receive `PB_DAEMON_URL` when the daemon configured one, so
`event complete` from inside a hook can hit the same API.

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
curl -s http://127.0.0.1:8080/healthz
# channels.running should list enabled adapters (e.g. "telegram")
```

---

## Troubleshooting

### Channel not receiving

1. Daemon process still running? UI / `curl …/healthz`.
2. `uvx pboy channel list` **with** `PB_DAEMON_URL` set — `secret=yes`, `enabled`, and
   **running** (not only stopped-in-store).
3. Telegram: token valid; BotFather bot started with `/start`; long-poll calls
   `deleteWebhook` on start — a competing webhook elsewhere will steal updates.
4. `PB_CHANNELS_ENABLED` / `PB_TELEGRAM_BOT_TOKEN` when set **override** store values
   (including empty) — check env in the daemon’s shell.
5. Slack: both `bot_token` and `app_token`; Socket Mode enabled on the Slack app.

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
| Hook registered but daemon never runs it | CLI without `PB_DAEMON_URL` wrote a different DB |
| UI empty, CLI `event list` has rows | Same |
| Channel enabled in CLI, `/healthz` not running it | Enable hit local store; daemon never saw it |

Fix: export `PB_DAEMON_URL=http://127.0.0.1:8080` in every operator shell, or stop using
CLI against the default path while a daemon is up.

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
| [`../README.md`](../README.md) | Quick start |
| [`security-contract.md`](security-contract.md) | Local-only threat model |
| [`architecture-channels.md`](architecture-channels.md) | Wake-up vs Jira agent REST |
| [`../src/pboy/channels/README.md`](../src/pboy/channels/README.md) | Telegram / Slack setup detail |
| [`releasing.md`](releasing.md) | Maintainers / PyPI |
