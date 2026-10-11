# Core — `pboy.core`

`pboy.core` — 이벤트 오케스트레이션 핵심 엔진

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `config.py` | 설정 관리 (env vars, `.env`) |
| `normalizer.py` | 이벤트 정규화 (CloudEvents v1.0 변환) |
| `router.py` | Hook Router (필터/토픽 매칭) |
| `lease.py` | Concurrency/Lease Engine (동시 실행 제어) |
| `engine.py` | 오케스트레이터 (normalizer + router + lease 통합) |

## 아키텍처

```
┌─────────────────────────────────────────────────────┐
│                    Engine                            │
│                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ Normalizer  │→│   Router     │→│   Lease    │ │
│  │ (CloudEvent)│  │ (Filter/    │  │ (Concurrent│ │
│  │ 변환)       │  │  Topic 매칭) │  │  exec)     │ │
│  └─────────────┘  └──────────────┘  └────────────┘ │
│         ▲                                    │      │
│         └──────── Store (SQLite AES) ◄───────┘      │
└─────────────────────────────────────────────────────┘
```

## 핵심 인터페이스

### EventNormalizer
- `normalize(raw_payload: dict, source: str) -> CloudEvent` — Jira/Slack/TG/Webhook → CloudEvents v1.0

### HookRouter
- `match(event: CloudEvent) -> list[Hook]` — 토픽/필터 매칭

### LeaseEngine
- `acquire(hook_name: str) -> LeaseToken` — 동시 실행 제한
- `release(token: str)` — 실행 종료 시 회수
