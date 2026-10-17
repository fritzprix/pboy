# UI — `pboy.ui`

Embedded local dashboard mounted when `pboy daemon --ui` or `pboy ui` runs.

## Layout

| Path | Role |
|------|------|
| `mount.py` | Routes: overview, settings forms, HTMX partials |
| `agent_brief.py` | English agent brief (copy-to-clipboard source of truth) |
| `guides.py` | Layman copy, official links, field tooltips |
| `formatting.py` | Jinja filters: local time + relative hint, short IDs, durations |
| `templates/` | Jinja2 pages + partials |
| `static/` | CSS + vendored HTMX |

## Pages

| Path | Role |
|------|------|
| `/ui` | Overview — agent brief (collapsed once a hook exists) + hooks / events / executions; IDs link to the event page |
| `/ui/events/{id}` | Event payload, completion, and each execution's status / exit / duration with stdout and stderr (same data as `pboy event get --verbose`) |
| `/ui/agent-brief.txt` | Plain-text agent brief (same body as the copy button) |
| `/ui/settings` | Channel tokens + enable/disable; Telegram bot setup and account connect each show one step at a time (bot: token → enable → pair → protected; App API: keys → phone → code → 2FA if asked → connected); optional Jira harness secrets |
| `/ui/static/*` | Assets |

## Agent brief

The overview page includes a Korean layman blurb and a **Copy agent brief** control.
The brief itself is English and lives in `agent_brief.py` so it stays versioned with
CLI/API reality. Operators paste it into Cursor (or similar) so an agent can help with
settings, a session skill under `.cursor/skills/`, and hook subscription.

Secrets are accepted via POST forms and **never rendered back** in full (only “저장됨”).

Hook output is different: `/ui/events/{id}` shows execution stdout/stderr as stored,
the same trust level as `GET /v1/events/{id}` and `pboy event get --verbose` (local
operator only, see Access). Hooks should not print secrets.

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
