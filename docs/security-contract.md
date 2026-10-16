# Security contract (local-only)

Pageboy is a **single-user local app**. There is no login and no shared daemon
secret (`PB_AUTH_TOKEN` was removed). Safety comes from *where* the process
listens and *who* may talk to it.

## Bind

- The daemon listens on **loopback only** (`127.0.0.1`, `::1`, other
  `ipaddress` loopback literals, or the name `localhost`).
- Binding `0.0.0.0`, `::`, a LAN address, or a DNS name (including
  `127.evil.com`) is refused at startup.
- **Never put a reverse proxy in front of Pageboy.** A same-host proxy that
  rewrites `Host` to `localhost` makes every remote client look local. LAN,
  Docker published ports, and reverse proxies are **out of scope**.

## Every HTTP / WebSocket request (`LocalOnlyGuard`)

Applied as ASGI middleware on **all** routes (API, UI, static, `/healthz`):

1. **Peer** must be a loopback IP (`ipaddress…is_loopback`), or the Starlette
   test alias `testclient`.
2. **`Host`** must be exactly `localhost` or a loopback **IP literal** —
   anti DNS-rebinding. Names that merely *start with* `127.` (e.g.
   `127.evil.com`) are rejected.
3. **Unsafe HTTP methods** (`POST` / `PUT` / `PATCH` / `DELETE`):
   - `Sec-Fetch-Site` must be empty, `same-origin`, or `none` (blocks
     `cross-site` browser form posts).
   - If `Origin` is present, its host+port must match the request `Host`.
4. **WebSocket** handshakes always require a matching `Origin` (scopes have no
   HTTP method, so they are not treated as safe GETs).

Failed checks return **403** (WebSocket: close 1008). There is no token header
or cookie login.

Requests without `Origin` / `Sec-Fetch-Site` (curl, CLI) are allowed on unsafe
methods when peer + Host are local — browsers send those headers on
cross-origin POSTs.

## Multi-user / local processes (accepted)

Without a shared secret, **any OS user or local process** that can open a
loopback TCP connection to the daemon can:

- read events / hooks / secret *existence*
- `PUT` secrets, inject events, create hooks

Hook creation means **command execution** as the daemon’s OS user via the
harness. That is inherent to local-only Zero-Ops. Do **not** run the daemon on
a shared multi-user machine you do not trust, or under an account with secrets
you are unwilling to expose to other local accounts.

## Harness subprocess env

Hooks may receive:

| Variable | When |
|----------|------|
| `PB_EVENT_ID`, `PB_SOURCE`, `PB_HOOK_NAME` | Always |
| `PB_DAEMON_URL` | When the daemon configured a public URL |
| `PB_LEASE_TOKEN` | When a lease was issued |

They never receive a master auth secret. Callbacks to the daemon still require
loopback + local `Host` like any other client.

## Operator expectations

- Run on the same machine as the browser/CLI (`uvx pboy ui`).
- Point CLI at the daemon with `PB_DAEMON_URL=http://127.0.0.1:8080` when needed.
- Do not publish the port, and do not reverse-proxy it.

## Production security checklist

Operator-facing checklist for a **local** Alpha install. There is **no**
`PB_AUTH_TOKEN` and no login — items below replace “set a daemon secret”.

| Check | Expectation |
|-------|-------------|
| Bind | Default / only `127.0.0.1` (or other loopback). Startup must refuse `0.0.0.0`, LAN IPs, and non-loopback DNS names. |
| Exposure | Do **not** publish the port (Docker `-p`, cloud firewall, reverse proxy, SSH `-L` to untrusted clients). |
| Reverse proxy | **Forbidden.** Rewriting `Host` to `localhost` defeats `LocalOnlyGuard`. |
| Host / machine | Single-user machine you trust. Other local OS users/processes can call the API and create hooks → **command execution** as the daemon user. |
| Secret key | `~/.config/pboy/secret.key` is created mode `0600`. Do not chmod it world-readable; prefer not sharing the home directory. |
| DB / files | Default DB `~/.local/share/pboy/pboy.db`; channel files under `…/files/`. Treat as sensitive (events, secrets ciphertext, attachments). |
| Channel tokens | Store via UI/CLI/env; never commit tokens. Env overrides store when non-empty (see operator guide). |
| Telegram allowlist | For any non-toy bot, set the allowlist in `/ui/settings` (6-digit pairing, or the allowlist field). When empty, **any** Telegram chat that messages the bot can trigger `telegram:message` hooks. Pair in a direct chat (in groups the pairing reply is visible to members; verification still needs local loopback). |
| Hooks | Only register `exec` you intend to run; harness runs as the daemon OS user with full local privileges of that account. Do not inline secrets in `hook.exec` command strings (spawn failures surface `hook.exec` and `argv` in execution `stderr`). |
| CLI split-brain | Set `PB_DAEMON_URL=http://127.0.0.1:<port>` whenever the daemon is up so CLI mutations hit the same DB. |
| Health | `curl -s http://127.0.0.1:8080/healthz` from the same host only. |

## Outbound / SSRF policy (Telegram)

Pageboy does **not** accept operator- or event-controlled HTTP URLs for Telegram
I/O.

| Path | URL source | Guarantee |
|------|------------|-----------|
| Inbound long-poll / `getFile` download | Hard-coded `DEFAULT_API_BASE` (`https://api.telegram.org`) + bot token + Bot API paths. `api_base` is a constructor/test inject only — **not** settings, env, or CloudEvent fields. | No user-controlled request URL. |
| Outbound `sendMessage` / `sendPhoto` / … | Same fixed `api_base` + fixed method names. Chat/message targets come from event fields (`channel_id`, `entity_key`) as Telegram IDs, not URLs. | No user-controlled request URL. |
| Reply `file_path` | Local path only; must resolve under `data_dir/files` (`resolve_reply_file_path`). | No arbitrary local file read (LFI) via complete API. |
| Reply `file_id` | Passed to Telegram as a Bot API file id string, not fetched as an HTTP URL by Pageboy. | N/A |

Slack uses the official Socket Mode / Web API clients with tokens from env/store —
same idea: no event-payload URL open redirects.

**Not in scope for Alpha:** a runtime allowlist beyond the fixed Telegram base.
The explicit guarantee is *no user-controlled URLs*, not “call any host on an
allowlist”.

## Known limits (Alpha)

- **No session cookies / no UI login** — the old “cookie not `Secure` on
  `http://127.0.0.1`” note is obsolete; there is nothing to mark `Secure`.
- **Any local peer** that can open the loopback port has full API access
  (read events, put secrets, create hooks). `GET /v1/events/{id}` returns up to
  100 execution records including `stdout`/`stderr` (and spawn failure diagnostics
  include `hook.exec` + `argv`), so treat event/execution APIs as full local trust.
- **Default Telegram inbound is open until allowlisted** — an empty
  `telegram.allowlist` accepts messages from any chat/user. Always configure the
  allowlist (or pair via `/ui/settings`) before attaching sensitive hooks.
- **Harness = RCE as daemon user** by design once a hook is registered.
- **Inbound/outbound file retention** under `data_dir/files/` is not pruned
  automatically (see issue #30).
- **Multi-tenant / public SaaS / LAN listen** are out of scope; do not stretch
  this process into those deployments.
