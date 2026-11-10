# v1.7.3 — 요청서 사진 인식의 30초 응답 대기 제한 수정

## 문제와 범위

2026-11-10 운영에서 월간 요청서 사진 업로드가 "Gemini API 호출에 실패했습니다"로 끝났다.
운영 로그에는 `AI 실패 code=network` 한 건이 남았다.

- 기준 source SHA: `37faa0c258e0eac32913faf003f391024a419a02` (v1.7.2).
- 작업 브랜치: `feature/gemini-read-timeout`.
- 원인: `app/ai/gemini.py`가 HTTP 대기 한도를 `min(timeout, 30)`으로 고정했다.
  `generateContent`는 생성이 끝난 뒤에야 첫 바이트를 보내는데, 78행 합성 표는 응답까지
  55~98초가 걸렸다. `AI_TIMEOUT_SECONDS`를 올려도 30초 상한은 그대로였다.
- 키·DNS·TLS·모델(`gemini-3.6-flash`) 조회는 정상이었다. 같은 날 Gemini는 혼잡 503도
  간헐적으로 반환했다(진단 호출 약 15건 중 6건).
- 금액 계산, 식별자, 검수·확정 계약, DB 스키마, 인식 프롬프트는 바꾸지 않는다.
- PR source SHA와 필수 CI 결과는 PR 본문에서 연결한다.

## 변경

- 응답 대기를 호출의 남은 시간 예산 전체까지 허용하고 연결 단계만 10초로 제한한다.
- 응답 대기·전송 초과는 `timeout` 코드와 "AI 인식 시간이 초과되었습니다" 문구로 구분한다.
  연결 실패·연결 시간 초과·프로토콜 오류는 기존 `network`를 유지한다.
- 500·502·503·504는 3초, 6초 뒤 최대 두 번 다시 보낸다. 대기가 남은 예산을 넘으면
  다시 보내지 않는다. 4xx와 시간 초과는 재시도하지 않는다.
- `AI_TIMEOUT_SECONDS` 기본값을 90초에서 240초로 올렸다. 원표 인식과 이름 재확인이 예산을
  함께 쓰는 계약과 초과 시 처음 읽은 표를 보존하는 동작은 유지한다.
- `GEMINI_MODEL` 기본값을 제공이 끝난 `gemini-2.5-flash`(해당 키에서 404)에서 운영이 쓰는
  `gemini-3.6-flash`로 맞췄다.
- 업로드 대기 문구에 예상 소요 시간을 넣었다. 앱·README·버전 검사를 v1.7.3으로 맞췄다.

## 진단과 검증

실제 요청서·장부는 외부로 보내지 않았다. Gemini 호출은 모두 합성 78행 PNG를 사용했다.

- 수정 전 재현(운영 컨테이너의 일회성 프로세스, 운영 코드 경로): 3회 모두 30.2초에
  `ReadTimeout` → `code=network`.
- 수정 전 대기 한도만 늘린 측정: 55.6초, 69.5초에 78행 정상 반환.
- 수정 코드의 실제 호출(같은 방식의 일회성 프로세스, 예산 180초): 원표 인식 78행 98.1초,
  이름 재확인 78행 50.0초. 이 편차를 근거로 기본 예산을 240초로 정했다.
- 자동 검사는 운영 `.env`·DB 없는 격리 worktree, Python 3.13, Mock, 임시 `DATABASE_PATH`에서
  실행했다. `tests/test_gemini.py`에 다음 실패 재현을 추가했고 수정 전 실패를 확인했다.
  - 응답 대기 한도가 예산 전체이고 연결 한도가 10초 이하
  - `ReadTimeout`·`WriteTimeout`이 `timeout`으로 기록되고 다시 보내지 않음
  - 5xx 재시도 성공, 세 번째 실패 후 중단, 4xx 미재시도, 예산을 넘는 대기 없음
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app scripts`,
  `uv run pytest --cov=app --cov-fail-under=85`, `alembic upgrade head`·`alembic check` 결과는
  PR의 CI 실행으로 연결한다.

## PR CI에서 드러난 기존 문제

첫 CI 실행에서 이번 변경과 무관한 두 항목이 실패해 같은 PR에서 최소 수정했다.

- `tests/test_ledger_corrections.py::test_adjustment_is_separate_observation_and_blocks_propagation`:
  보정 관측을 실제 현재 월로 기록해 2026-11부터 `2026-10` 계산에서 빠졌다(`-70 != -25`).
  같은 파일의 다른 검사처럼 `current_month`를 `2026-10`으로 고정했다. 장부 코드는 바꾸지 않았다.
- `security`(pip-audit): 개발 전용 의존성 `filelock` 3.32.2, `msgpack` 1.2.1에 새 권고가 등록됐다.
  둘 다 `pip-audit` 하위 의존성이며 운영 이미지에는 없다. `uv lock --upgrade-package`로
  `filelock` 4.2.10, `msgpack` 1.2.4로 올렸고 로컬 `uv run pip-audit`는 알려진 취약점 없음이었다.

## 한계

- 이름 재확인이 남은 예산을 넘겨 끊기면 worker는 자체 기한까지 슬롯을 점유한다. 이전에는
  30초 상한이 이 시간을 줄였다. 동시 실행 한도와 "재확인 불가" 안내는 그대로다.
- 사진 한 장에 1~4분이 걸릴 수 있다. 사고량(thinking) 축소는 인식 동작을 바꾸므로 넣지 않았다.
- Gemini 한도 소진(429)은 재시도하지 않는다. 다른 제공자로의 전환은 후속 작업이다.
- Win7 Chrome 109·Android 실기에서 긴 대기 중 화면 동작은 확인하지 않았다.
