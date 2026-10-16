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
