# UI — `pboy.ui`

Embedded local dashboard mounted when `pboy daemon --ui` or `pboy ui` runs.

## Layout

| Path | Role |
|------|------|
| `mount.py` | Routes: overview, settings forms, HTMX partials |
| `agent_brief.py` | English agent brief (copy-to-clipboard source of truth) |
| `guides.py` | Layman copy, official links, field tooltips |
| `templates/` | Jinja2 pages + partials |
| `static/` | CSS + vendored HTMX |

## Pages

| Path | Role |
|------|------|
| `/ui` | Overview — agent brief + hooks / events / executions |
| `/ui/agent-brief.txt` | Plain-text agent brief (same body as the copy button) |
| `/ui/settings` | Channel tokens + enable/disable + optional Jira harness secrets |
| `/ui/static/*` | Assets |

## Agent brief

The overview page includes a Korean layman blurb and a **Copy agent brief** control.
The brief itself is English and lives in `agent_brief.py` so it stays versioned with
CLI/API reality. Operators paste it into Cursor (or similar) so an agent can help with
settings, a session skill under `.cursor/skills/`, and hook subscription.

Secrets are accepted via POST forms and **never rendered back** in full (only “저장됨”).

## Access

There is **no login**. The daemon’s `LocalOnlyGuard` (see
[`docs/security-contract.md`](../../../docs/security-contract.md)) applies to every
UI route:

- Loopback peer + local `Host` (`127.0.0.1` / `localhost` / `::1` IP literals)
- Non-local peer or Host (including `127.evil.com`) → **403**
- UI POSTs with `Sec-Fetch-Site: cross-site` (or a foreign `Origin`) → **403**
- Non-loopback bind is refused at startup
- Do not reverse-proxy the UI

## Operator entry

```bash
pboy daemon --host 127.0.0.1 --port 8080 --ui
# http://127.0.0.1:8080/ui
```
