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

- **Loopback** read: always allowed.
- **Non-loopback** read: token / signed session cookie / `?token=`.
- **Mutations** (save secret, enable channel): if `PB_AUTH_TOKEN` is set, login
  cookie (HMAC session) or header required even on loopback.
- **CSRF**: UI POSTs with `Sec-Fetch-Site: cross-site` (or a foreign `Origin`) are
  rejected — mitigates evil.com → `127.0.0.1` form posts when no auth token is set.
- **Remote access**: prefer loopback or put TLS (Caddy/nginx) in front; the session
  cookie is `HttpOnly` + `SameSite=Lax` but not `Secure` (plain `http://127.0.0.1`).

## Operator entry

```bash
export PB_AUTH_TOKEN=dev-secret
pboy daemon --host 127.0.0.1 --port 8080 --ui
# http://127.0.0.1:8080/ui/settings
```
