# Channels — `pboy.channels`

`pboy.channels` — 외부 플랫폼 채널 수신부

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `jira.py` | Jira Cloud 웹훅 수신 (Cloudflare Relay 중계) |
| `confluence.py` | Confluence Cloud 웹훅 수신 (Cloudflare Relay 중계) |
| `telegram.py` | Telegram Bot API Long-polling |
| `slack.py` | Slack Socket Mode (WebSocket) |

## 통신 방식

| 채널 | 방식 | 터널링 필요 | 설정 항목 |
|------|------|------------|----------|
| Telegram | Long-polling (`getUpdates`) | ❌ | Bot Token |
| Slack | Socket Mode (`xapp-`/`xoxb-`) | ❌ | App Token, Bot Token |
| Jira | Webhook → Relay | ❌ | 고정 URL 1 회 복사 |
| Confluence | Webhook → Relay | ❌ | 고정 URL 1 회 복사 |

## Cloudflare Relay 흐름

```
Jira Webhook → relay.pboy.dev/wh/{user_id}
                                    │
                         WebSocket (wss://relay.pboy.dev/ws/{user_id})
                                    │
                              pboy Local Daemon
                                    │
                         EventNormalizer → CloudEvents v1.0
```

## 공통 인터페이스

```python
class Channel:
    async def start() -> None          # 수신 시작
    async def stop() -> None           # 수신 종료
    async def normalize(raw: dict) -> CloudEvent  # 정규화
```
