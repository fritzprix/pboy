# Changelog

All notable changes to Pageboy (`pboy`) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [SemVer](https://semver.org/) (0.x = Alpha; breaking changes allowed with a minor bump until 1.0).

## Versioning policy

| Change | Bump | Example |
|--------|------|---------|
| Bug fix, docs, CI | patch | `0.1.0` → `0.1.1` |
| New feature (backward compatible at Alpha) | minor | `0.1.0` → `0.2.0` |
| Breaking CLI/API/store change (pre-1.0) | minor | `0.2.0` → `0.3.0` |
| First stable public API | major | `0.x` → `1.0.0` |

**Release process:** see [`docs/releasing.md`](docs/releasing.md).  
**Alpha classifiers** stay until an explicit decision to leave Alpha (not tied to the first PyPI publish).

## [Unreleased]

## [0.1.0] — 2026-10-14

First publishable Alpha.

### Added

- Core orchestrator: CloudEvents normalize → store → hook match → lease → subprocess harness
- Local daemon (`pboy daemon`) with `/healthz`, `/v1/*` inject/CRUD APIs, optional `PB_AUTH_TOKEN`
- Daemon-aware CLI when `PB_DAEMON_URL` is set (`hook` / `state` / `event` / `channel`)
- Wake-up channels: Telegram long-poll, Slack Socket Mode
- `pboy event complete` with Telegram outbound sync
- Embedded dashboard (`pboy ui` / `pboy daemon --ui`): hooks, events, channel/secret forms
- AES-256-GCM secret storage; secret key file mode `0600`
- Operator smoke script (`scripts/smoke_demo.sh`)
- GitHub Actions CI (ruff, mypy, pytest, contract, smoke)

### Notes

- Jira/Confluence: agent REST from harness (no required webhook ChannelAdapter)
- Schema migrations deferred until a real installed base needs them

[Unreleased]: https://github.com/fritzprix/pboy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/fritzprix/pboy/releases/tag/v0.1.0
