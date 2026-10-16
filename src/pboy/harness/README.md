# Harness — `pboy.harness`

`pboy.harness` — 이벤트 기반 하네스 subprocess executor

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `executor.py` | subprocess fork/exec, stdin/stderr/stdout 파이프 |
| `watchdog.py` | 프로세스 타임아웃, 강제 종료 |
| `reporter.py` | `pboy state put`/`pboy event complete` 통신 |

## 실행 흐름

```
1. EventRouter 가 Hook 매칭 → executor.spawn()
2. subprocess.Popen(stdin=PIPE, stdout=PIPE, stderr=PIPE)
3. CloudEvents JSON → stdin 주입
4. env 주입: PB_EVENT_ID, PB_SOURCE, PB_HOOK_NAME, PB_LEASE_TOKEN
5. 하네스 실행 (Python, Rust, Shell, Node 등)
6. watchdog 타임아웃 모니터링
7. 종료 시: exit_code, stdout/stderr 로깅, execution record 저장
8. Lease.release() — 동시 실행 슬롯 회수
```

## 환경 변수 주입

| 변수 | 설명 |
|------|------|
| `PB_EVENT_ID` | 이벤트 고유 ID |
| `PB_SOURCE` | 원천 플랫폼 (`jira`, `confluence`, `slack`, `telegram`) |
| `PB_HOOK_NAME` | 매칭된 훅 명칭 |
| `PB_LEASE_TOKEN` | 세션 전용 임시 토큰 |

## 하네스 표준 레퍼런스

```python
#!/usr/bin/env python3
import sys, json, os, subprocess

def run_pboy(*args):
    return subprocess.run(["pboy", *args], check=True, text=True, capture_output=True)

def main():
    # Stdin: CloudEvents JSON
    event = json.loads(sys.stdin.read())
    event_id = os.environ["PB_EVENT_ID"]

    # 진행 상태 보고
    run_pboy("state", "put", "--event-id", event_id,
             "--status", "running", "--progress", "10")

    # 비즈니스 로직
    result = "Diagnosis complete"

    # 완료 처리 + 멀티 채널 동시 회신
    run_pboy("event", "complete", event_id,
             "--summary", result, "--sync", "jira:comment,slack:thread")

if __name__ == "__main__":
    main()
```
