# Pageboy (`pboy` / `pb`)

로컬에서 Telegram·Slack **wake-up**을 받아, 매칭된 훅(`exec`)을 실행하는 Zero-Ops 이벤트 오케스트레이터입니다. (**Alpha · [PyPI](https://pypi.org/project/pboy/)**)

Jira/Confluence는 데몬 webhook이 아니라, 훅 프로그램이 REST로 다루는 작업면입니다. → [`docs/architecture-channels.md`](docs/architecture-channels.md)

## 설치 · 실행

```bash
uvx pboy ui
# 또는: uvx pboy@0.1.0 ui
```

브라우저: [http://127.0.0.1:8080/ui](http://127.0.0.1:8080/ui)

로그인 없음 — 이 머신(loopback)에서만 API·UI에 접근할 수 있습니다.

| 경로 | 역할 |
|------|------|
| `/ui` | 훅 · 이벤트 · 실행 기록 |
| `/ui/settings` | Telegram / Slack 토큰 · 채널 on/off |

데이터 기본 위치: DB `~/.local/share/pboy/pboy.db` · 암호화 키 `~/.config/pboy/secret.key`

## 첫 시나리오 (Telegram)

1. [@BotFather](https://t.me/BotFather)에서 봇 토큰 발급  
2. `/ui/settings` → Telegram 토큰 저장 → **Enable**  
3. 훅 등록 (데몬이 떠 있는 터미널과 다른 셸):

```bash
export PB_DAEMON_URL=http://127.0.0.1:8080
uvx pboy hook register \
  --name tg-echo \
  --topic 'telegram:message' \
  --exec 'echo hello-from-pboy' \
  --timeout 30
```

Windows에서 `echo`가 안 되면: `--exec 'cmd /c echo hello-from-pboy'`

4. 텔레그램에서 봇에게 메시지 → `/ui`에 이벤트·실행 표시  
5. (선택) 봇으로 답장:

```bash
uvx pboy event complete <event_id> --summary 'Resolved' --sync telegram
```

Slack은 Socket Mode용 **bot 토큰(`xoxb-`)** + **앱 토큰(`xapp-`, `connections:write`)** 둘 다 필요합니다. 자세한 절차: [`src/pboy/channels/README.md`](src/pboy/channels/README.md)

## Operator guide

배포·재시작, happy-path 체크리스트, 채널/훅/`PB_DAEMON_URL` split-brain·403 트러블슈팅:
[`docs/operator.md`](docs/operator.md)

## 보안 (짧게)

Pageboy는 **local-only**입니다. loopback 바인드만 허용하고, 모든 요청은 같은 머신 + 로컬 `Host`여야 합니다. LAN/`0.0.0.0`은 지원하지 않습니다. **리버스 프록시를 앞에 두지 마세요** (Host를 localhost로 바꾸면 원격 클라이언트가 로컬로 보입니다). 같은 머신의 다른 사용자·프로세스도 데몬에 닿을 수 있습니다.

운영 체크리스트 · Telegram SSRF 정책(고정 `api.telegram.org`, 사용자 제어 URL 없음) · 알려진 한계:
[`docs/security-contract.md`](docs/security-contract.md)

CLI가 같은 데몬 DB를 쓰려면 `PB_DAEMON_URL=http://127.0.0.1:8080`을 맞추세요. 없으면 CLI가 **별도 로컬 DB**를 써서 split-brain이 납니다.

## 자주 쓰는 명령

```bash
uvx pboy --help
uvx pboy channel list
uvx pboy hook register --name ... --topic 'telegram:message' --exec '...' --timeout 30
uvx pboy event list
uvx pboy event complete <id> --summary '...' --sync telegram
```

환경변수 (`PB_` prefix, 모두 선택):

| 변수 | 용도 |
|------|------|
| `PB_DAEMON_URL` | CLI → 데몬 HTTP |
| `PB_TELEGRAM_BOT_TOKEN` / `PB_SLACK_*` | UI 대신 env로 채널 토큰 |
| `PB_CHANNELS_ENABLED` | 켜진 채널 (설정 시 store enable과 충돌 주의) |

## 개발자

```bash
git clone https://github.com/fritzprix/pboy.git
cd pboy
uv sync --extra dev
uv run pboy ui

uv run pytest -q
uv run ruff check src tests
uv run mypy src
./scripts/smoke_demo.sh
```

운영: [`docs/operator.md`](docs/operator.md) · 보안: [`docs/security-contract.md`](docs/security-contract.md)  
릴리스: [`docs/releasing.md`](docs/releasing.md) · 스킬 `release patch|minor|major`  
로드맵: [`ROADMAP.md`](ROADMAP.md) · [Issues](https://github.com/fritzprix/pboy/issues)  
UI/채널 상세: [`src/pboy/ui/README.md`](src/pboy/ui/README.md) · [`src/pboy/channels/README.md`](src/pboy/channels/README.md)

### 필터

지원: `.path.to.field == "value"` / `!=`  
그 외 표현식은 fail-closed (매칭 안 함).

## License

Apache-2.0
