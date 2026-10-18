# Harness — `pboy.harness`

`pboy.harness` — 이벤트 기반 하네스 subprocess executor

## 모듈 구조

| 파일 | 설명 |
|------|------|
| `executor.py` | subprocess fork/exec, stdin/stderr/stdout 파이프 |
| `watchdog.py` | 프로세스 타임아웃, 강제 종료 |
| `reporter.py` | `pboy state put`/`pboy event complete` 통신 |
| `hook_contract.py` | 등록 성공 힌트 · `pboy hook template` 스캐폴드 |

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
| `PB_LEASE_TOKEN` | 리스 토큰(발급 시); 훅 API가 소비하지 않음 — 보통 무시 |

## 하네스 표준 레퍼런스

스캐폴드 생성: `pboy hook template --lang python` (또는 `--lang bash`).

```python
#!/usr/bin/env python3
import json
import os
import subprocess
import sys


def main() -> None:
    event = json.load(sys.stdin)
    source = os.environ.get("PB_SOURCE", "")
    summary = (event.get("data") or {}).get("summary") or ""

    subprocess.run(
        ["pboy", "state", "put", "--status", "running", "--progress", "10"],
        check=False,
    )

    result = f"Handled ({source}): {summary}" if summary else f"Handled ({source})"

    # telegram/slack: event reply picks --sync from PB_SOURCE / event.source
    if source in {"telegram", "slack"}:
        subprocess.run(["pboy", "event", "reply", "--summary", result], check=False)
    else:
        # jira/confluence: complete without chat sync
        subprocess.run(["pboy", "event", "complete", "--summary", result], check=False)


if __name__ == "__main__":
    main()
```
