# Pageboy production roadmap

Backlog lives on GitHub Issues with milestones **M1–M6**.  
Repo: https://github.com/fritzprix/pboy/issues

## Current vs production

| Capability | Now (Alpha) | Production target |
|---|---|---|
| Hook / state / event CLI | ✅ | ✅ |
| Engine normalize→route→exec | ✅ | ✅ |
| Long-running daemon + inject/hooks API | ✅ | ✅ M1 |
| Real inbound channel | ❌ | ✅ M2 (Telegram first) |
| `event complete` + outbound sync | ❌ | ✅ M3 |
| Slack / Jira / Confluence / relay | ❌ | ✅ M4 |
| Web UI | ❌ | ✅ M5 |
| CI, packaging, hardening, ops docs | ❌ | ✅ M6 |

## Sequential plan (implement → verify → next)

```text
M1 Daemon spine
  #1  Daemon + /healthz          ✅
  #2  POST /v1/events inject     ✅
  #3  PB_DAEMON_URL-aware CLI    ✅
  #19–#23 Audit remediation      ✅ (cli shadowing, daemon URL, hooks API, auth, tests/docs)
  #4  Schema migrations          ← next
       │
M2 First channel
  #5  ChannelAdapter interface
  #6  Telegram long-poll E2E
  #7  channel CLI config
       │
M3 Outbound
  #8  event complete persist
  #9  sync telegram reply
       │
M4 Multi-channel
  #10 Slack Socket Mode
  #11 Jira/Confluence + relay
  #12 outbound jira/slack
       │
M5 Web UI
  #13 dashboard shell
  #14 secrets/channel forms
       │
M6 Hardening (can start early in parallel)
  #15 CI workflow                ✅
  #16 PyPI / uvx release
  #17 security checklist
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
- Do not start UI-heavy work before M1.2 (something real to display).
- Prefer Telegram as first channel (outbound poll, no tunnel).
