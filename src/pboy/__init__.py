# Pageboy (`pboy` / `pb`)
"""
pboy — Zero-Ops Agent Event Orchestrator & Dispatcher

Slack, Telegram, Jira, Confluence 의 멀티 채널 이벤트를 로컬 머신에서 수신하고,
이벤트 구동 방식으로 에이전트 하네스 (Harness) 를 실행·동기화하는 도구입니다.

사용법:
    uvx pboy daemon --ui                  # 데몬 + 웹 UI 시작
    pboy hook register --name ... --exec ...  # 훅 등록
    pboy state put --event-id ... --status running  # 하네스 내부 상태 보고
    pboy event complete --event-id ... --summary ...  # 완료 처리

패키지 구조:
    cli/       — CLI 명령어 (hook, state, event, daemon, ui)
    core/      — EventNormalizer, HookRouter, LeaseEngine
    channels/  — Telegram, Slack, Jira/Confluence 수신부
    store/     — SQLite AES 암호화 저장소
    harness/   — Subprocess executor (fork/exec)
    ui/        — 임베디드 로컬 웹 대시보드
    models/    — CloudEvents v1.0 호환 도메인 모델
"""

__version__ = "0.1.0"
__author__ = "fritzprix"
