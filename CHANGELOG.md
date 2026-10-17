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

## [0.3.1] — 2026-10-17

Localized dashboard and settings UI, Slack thread replies, and a single
`pboy event reply` command for chat replies.

### Added

- Dashboard and settings UI in English, Korean, Japanese, Simplified Chinese,
  French, and Spanish, with a header language picker (`pboy_lang` cookie, falling
  back to `Accept-Language`, then English). Flash messages, Telegram login/pairing
  errors, and the pairing-code bot message are localized too.
- `pboy event reply` and `pboy telegram reply` (alias `send`) — shortcuts for
  chat replies. `event reply` picks `telegram` or `slack` from the event source;
  `pboy telegram reply` stays bot-token specific. Listed under
  `pboy telegram --help` so the Commands table is not read-only-looking
  (chats/messages/download/export alone). `pboy channel list` prints reply tips on
  stderr.
- Slack outbound sync (`--sync slack` / `chat.postMessage` thread reply) so Slack
  is receive+reply like Telegram. Reply files stay Telegram-only (rejected up front
  for Slack; the HTTP API answers 422). Slack CloudEvents now include
  `data.extra.ts` (message timestamp) for outbound thread anchoring;
  `data.thread_id` still means “already inside a thread” only. Events stored before
  this change have no `extra.ts`, so their replies post to the channel, not a thread.

### Changed

- The UI defaults to English when the browser prefers none of the supported
  languages (it was Korean-only before).

## [0.3.0] — 2026-10-17

Dashboard, settings UX, and Telegram file retention/queue hardening on the
local-only Alpha train.

### Added

- Event detail page (`/ui/events/{id}`) with payload and execution stdout/stderr;
  overview tables use short IDs, relative times, and status badges (#47).
- Copyable agent brief on the dashboard overview (#42).
- Step-by-step Telegram bot setup and App API account-connect flows in
  `/ui/settings` (#45, #44).
- Telegram poll emit queue backpressure (max pending tasks) so burst traffic cannot
  unbounded-grow asyncio tasks (#30). Pending-slot wait holds local offset until the
  next `getUpdates` (helps redelivery if the poll task is cancelled mid-wait); emit
  tasks already queued may still be cancelled after `stop()`'s drain timeout.
- `pboy files prune --older-than` (optional `--dry-run`) for staged
  `data_dir/files/{telegram,outbound,telegram_app}` cleanup; default retention
  remains forever / operator-managed (#30). Staged outbound copies use a fresh mtime
  so prune cannot delete a reply just staged from an old source file.

### Changed

- README restructured around the event → hook → reply flow (#41).

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

[Unreleased]: https://github.com/fritzprix/pboy/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/fritzprix/pboy/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/fritzprix/pboy/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fritzprix/pboy/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fritzprix/pboy/releases/tag/v0.1.0
