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

### Changed

- Default daemon port is now `17680` (was `8080`) to avoid clashes with common
  local HTTP stacks. CLI auto-probe and `pboy ui` / `pboy daemon` defaults follow
  the same port (`PB_PORT` / `--port` still override). Migration: if a daemon is
  still listening on `:8080`, the CLI prints a stderr hint (does not auto-connect)
  to pass `--daemon-url` or restart on `17680` (#62).

### Fixed

- Mutating CLI paths (`hook register`/`delete`, `channel enable`/`disable`/
  `set-secret`) warn when no daemon is reachable on the default URL and the
  command falls back to local SQLite — reduces silent split-brain when the
  daemon runs on a custom port (#60).
- `ensure_daemon_url` / `pboy mcp` reuse a leftover daemon on `:8080` instead of
  spawning a second process on the new default port (avoids dual channel
  pollers on one DB) (#64).

### Added

- MCP stdio server (`pboy mcp`) for agents: `list_events`, `get_event`,
  `reply_event`, `list_hooks`, `register_hook`, `delete_hook`, `status`,
  `telegram_list_chats`, `telegram_list_messages`. Event inject and secret
  writes are intentionally not exposed. Depends on `mcp>=2,<3`.
- Singleton daemon ensure: `pboy mcp` (and a no-op path via `pboy ui`) probe
  `/healthz` and spawn at most one background `pboy daemon --ui` if needed.
  The daemon is left running when MCP exits; a second `pboy ui` prints the
  existing URL instead of binding again.

## [0.3.4] — 2026-10-18

Hook authoring discoverability for AI agents: template scaffolds, register-time
contract hints, and an expanded agent brief.

### Added

- `pboy hook template --lang python|bash` prints a CloudEvent stdin / `PB_SOURCE`
  handler scaffold (optional `--topic` comment).
- `pboy hook register` success output now includes the stdin/env contract, topic →
  `PB_SOURCE` / CloudEvent `type`, normalized `.data` keys, and a template tip.
- Agent brief: new **Writing a hook script** section (topic↔type table, normalized
  `data` keys, minimal Python pattern, wildcard/`event inject --no-dispatch` notes).

### Changed

- `pboy hook` / `--exec` help text clarify normalized CloudEvent stdin (not
  platform-raw JSON) and point at `pboy hook template`.
- Harness README reference script uses `event reply` / `event complete` instead of
  the obsolete multi-target `--sync` example.

## [0.3.3] — 2026-10-18

### Changed

- Refined Korean message catalog (`src/pboy/i18n/locales/ko.json`) to eliminate literal translations (e.g. "열쇠" → "키", "작업면" → "연동 인터페이스") and overly casual UI wording (e.g. "한눈에" → "개요", "한 줄 요약:" → "요약:", "아직 없음" → "설정되지 않음", "토큰은 이렇게 받습니다" → "토큰 발급 방법") in favor of standard engineering dashboard terminology.

## [0.3.2] — 2026-10-18

### Added

- When running `pboy ui` or `pboy daemon --ui`, the UI access URL (`http://<host>:<port>/ui`) is logged upon daemon readiness, and root path (`/`) requests are redirected to `/ui` (HTTP 307) when UI mode is enabled.

### Fixed

- The settings UI shows a rejected Telegram bot token and channel-registry errors
  (unknown channel, empty secret) in the viewer's language; CLI/API text is unchanged.
- `Accept-Language` parsing accepts `Q=` and `q` after other parameters.

### Changed

- Dependency `typer[all]` → `typer` (the `all` extra no longer exists; `rich` and
  `shellingham` are regular typer dependencies).
- CI/release workflows: Node 24 action majors (`checkout@v7`, `setup-python@v7`,
  `upload-artifact@v7`, `download-artifact@v8`, `action-gh-release@v3`), runners
  pinned to `ubuntu-24.04`, and the wheel check also requires the i18n catalogs.

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

[Unreleased]: https://github.com/fritzprix/pboy/compare/v0.3.4...HEAD
[0.3.4]: https://github.com/fritzprix/pboy/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/fritzprix/pboy/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/fritzprix/pboy/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/fritzprix/pboy/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/fritzprix/pboy/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/fritzprix/pboy/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fritzprix/pboy/releases/tag/v0.1.0
