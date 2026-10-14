# UI — `pboy.ui`

Embedded local dashboard mounted when `pboy daemon --ui` or `pboy ui` runs.

## Layout

| Path | Role |
|------|------|
| `mount.py` | Routes: overview, settings forms, login, HTMX partials |
| `guides.py` | Layman copy, official links, field tooltips |
| `templates/` | Jinja2 pages + partials |
| `static/` | CSS + vendored HTMX |

## Pages

| Path | Role |
|------|------|
| `/ui` | Overview — hooks / events / executions (read-only refresh) |
| `/ui/settings` | Channel tokens + enable/disable + optional Jira harness secrets |
| `/ui/login` | Password = `PB_AUTH_TOKEN` (sets HttpOnly cookie) |
| `/ui/static/*` | Assets (no auth) |

Secrets are accepted via POST forms and **never rendered back** in full (only “저장됨”).

## Access

- **No `PB_AUTH_TOKEN`** (local-only): loopback peer + local `Host`
  (`127.0.0.1` / `localhost` / `::1`) — no login. Non-local `Host` → 403
  (DNS-rebinding defense). Bind beyond loopback without a token is refused at
  startup.
- **`PB_AUTH_TOKEN` set**: token / signed session required for **all** clients,
  including loopback. This is required when putting Caddy/nginx/Cloudflare Tunnel
  in front of `127.0.0.1` — otherwise the proxy peer looks like loopback and would
  bypass auth.
- **CSRF**: UI POSTs with `Sec-Fetch-Site: cross-site` (or a foreign `Origin`) are
  rejected.
- Session cookie is `HttpOnly` + `SameSite=Lax` but not `Secure` on plain `http://`.

## Operator entry

```bash
# Local-only (no token)
pboy daemon --host 127.0.0.1 --port 8080 --ui

# Any reverse proxy / LAN / wildcard bind — set a token (enforced for everyone)
export PB_AUTH_TOKEN=dev-secret
pboy daemon --host 127.0.0.1 --port 8080 --ui
# or: pboy daemon --host 0.0.0.0 --port 8080 --ui
# http://127.0.0.1:8080/ui/login
```
