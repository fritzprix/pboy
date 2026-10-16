# Pageboy production roadmap

Backlog lives on GitHub Issues with milestones **M1–M6**.  
Repo: https://github.com/fritzprix/pboy/issues

Architecture note (channels vs ticketing): [`docs/architecture-channels.md`](docs/architecture-channels.md)

## Current vs production

| Capability | Now (Alpha) | Production target |
|---|---|---|
| Hook / state / event CLI | ✅ | ✅ |
| Engine normalize→route→exec | ✅ | ✅ |
| Long-running daemon + inject/hooks API | ✅ | ✅ M1 |
| Wake-up channels (Telegram, Slack) | Telegram ✅ | ✅ M2 + M4.1 |
| `event complete` + Telegram outbound | ✅ M3 | ✅ |
| Jira/Confluence as agent REST work surface | docs ✅ | secrets + examples (no webhook required) |
| Jira webhook / relay | ❌ deferred | optional only |
| Web UI | ✅ shell + channel/secret forms | ✅ M5 |
| CI, packaging, hardening, ops docs | partial | ✅ M6 |

## Sequential plan (implement → verify → next)

```text
M1 Daemon spine
  #1  Daemon + /healthz          ✅
  #2  POST /v1/events inject     ✅
  #3  PB_DAEMON_URL-aware CLI    ✅
  #19–#23 Audit remediation      ✅
  #4  Schema migrations          ⏸ deferred (no installed DB — single DDL + delete DB to reset)
  #24 Operator smoke demo        ✅ (scripts/smoke_demo.sh + harness uvloop fix)
       │
M2 First channel
  #5  ChannelAdapter interface   ✅
  #6  Telegram long-poll E2E     ✅
  #7  channel CLI config         ✅
       │
M3 Outbound
  #8  event complete persist     ✅
  #9  sync telegram reply        ✅
       │
M4 Wake-up channels (revised)
  #10 Slack Socket Mode          ✅
  #11 Jira/Confluence webhook+relay  ⏸ deferred (agent REST pull instead)
  #12 outbound jira/slack        ⏸ jira sync deferred; slack thread optional later
       │
M5 Web UI
  #13 dashboard shell            ✅
  #14 secrets/channel forms      ✅

       │
M6 Hardening (can start early in parallel)
  #15 CI workflow                ✅
  #16 PyPI / uvx release         ← in progress (CHANGELOG + release.yml; first tag → PyPI)
  #17 security checklist         ← local-only contract (docs/security-contract.md + LocalOnlyGuard)
  #18 operator docs
```


## Verification gates (every issue)

1. `ruff check src tests`
2. `mypy src`
3. `pytest -q`
4. `pytest -m contract -q`
5. Issue-specific manual/integration check listed on the issue

## Working agreement

- One milestone slice at a time on `main` via PR (or direct commit for tiny CI/docs).
- ChannelAdapters are for **wake-up** transports (no inbound tunnel when possible).
- Jira/Confluence: prefer **agent harness REST**; do not block the roadmap on public webhooks.
- Prefer Telegram then Slack as wake-up channels.
