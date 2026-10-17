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

## [0.2.0] — 2026-10-17

Local-only Alpha train: security contract, Telegram bot + App API rails, store-only
secrets, operator docs, and CLI UX.

### Breaking

- Remove `PB_AUTH_TOKEN`, UI login (`/ui/login`), and non-loopback bind.
  The daemon is **local-only**: loopback listen + `LocalOnlyGuard` (peer, Host,
  mutation CSRF). See [`docs/security-contract.md`](docs/security-contract.md).
- Channel secrets / enablement are **store-only**: remove `PB_TELEGRAM_BOT_TOKEN`,
  `PB_TELEGRAM_ALLOWLIST`, `PB_SLACK_*`, and `PB_CHANNELS_ENABLED` overrides.
  Configure via `/ui/settings` or `pboy channel set-secret` / `enable` (#39).
  Daemon startup logs a loud error if those env vars are still set.
- CLI no longer uses `PB_DAEMON_URL`; it auto-connects to `http://127.0.0.1:8080`
  when the daemon is up (`--daemon-url` for a custom port) (#38).

### Added

- Telegram **App API** rail (Telethon): `api_id` / `api_hash` + account connect in
  `/ui/settings`; CLI `pboy telegram chats|messages|download|export` for user-account
  dialogs/history (separate from bot-token wake-up) (#39)
- Telegram inbound allowlist + 6-digit pairing UX in `/ui/settings` (#35)
- Telegram inbound attachments + `event complete` reply files (`--file-path` /
  `--file-id`; path confined under `data_dir/files`)
- Hook debug visibility (#27): spawn failures include `hook.exec` + `argv`;
  `event get --verbose` for execution streams
- Operator guide (#18) and production security checklist / SSRF policy (#17)
- OpenAPI field docs for inject/complete HTTP API (#28)
- CI on `dev/**`; auto-close issues when PRs merge into `dev/**`

### Fixed

- Host/bind allowlists use `ipaddress` loopback checks (reject `127.evil.com`)
- WebSocket handshakes require a matching `Origin`
- Windows-safe harness `exec` splitting

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

[Unreleased]: https://github.com/fritzprix/pboy/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/fritzprix/pboy/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fritzprix/pboy/releases/tag/v0.1.0
