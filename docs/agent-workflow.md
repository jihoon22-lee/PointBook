# 개발 에이전트 작업 안내

프로젝트의 상시 규칙은 [AGENTS.md](../AGENTS.md)에 둔다. 이 문서는 격리 실행,
작업별 도구 선택, 지침 검증에 필요한 절차를 설명한다.

## 작업 범위와 문서

v1.4.0의 범위·의존성은 [#47](https://github.com/jihoon22-lee/PointBook/issues/47),
R01~R26/S01~S12의 수용 기준은 [#48](https://github.com/jihoon22-lee/PointBook/issues/48),
실행 계획은 각 WP가 관리한다. 변경할 부분의 이슈를 읽고 현재 코드와 대조한다.
계획 본문을 로컬에 복제하거나 작업 이슈마다 PR을 만들 필요는 없다.

개발 기록은 PR 묶음당 `workthrough/YYYY-MM-DD-scope.md` 하나를 재사용한다.
문제·결정·관련 R/S/WP·PR·검증 SHA 또는 CI URL·실제 명령/환경·결과/한계를 남긴다.
준비 작업은 기능 수용 완료가 아니다. 실패·미실행·실환경 미확인을 구분한다.

## 격리된 개발과 검증

운영 checkout에는 `.env`와 DB가 있을 수 있다. 새로운 브랜치/worktree를 만들고
운영 `.env`·DB·원본 파일을 복사하지 않는다. worktree의 `.venv`는 `uv.lock`으로 만든다.
아래 명령은 **그 격리 worktree에서** 실행한다. `DATABASE_PATH`만 바꾸더라도 `.env`의
다른 설정을 읽을 수 있으므로 환경 파일이 없다는 확인도 필요하다.

```bash
uv sync --locked --group dev --python 3.13  # 현재 CI Python과 맞춤
uv run ruff check .
uv run ruff format --check .
uv run mypy app scripts
```

DB를 사용하는 검사 예시다. 수정 중에는 관련 테스트를 선택하고, 전체 검증 시 다음을 쓴다.

```bash
(
  set -eu
  test ! -e .env
  POINTBOOK_CHECK_ROOT="$(mktemp -d /tmp/pointbook-check.XXXXXX)"
  trap 'rm -rf "$POINTBOOK_CHECK_ROOT"' EXIT
  export DATABASE_PATH="$POINTBOOK_CHECK_ROOT/check.db"
  export AI_PROVIDER=mock
  export ADMIN_USERNAME=check-admin
  export ADMIN_PASSWORD=check-password
  export SECRET_KEY=check-secret-key
  uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85
  uv run alembic upgrade head
  uv run alembic check
)
```

개발 서버도 같은 격리 환경과 합성 관리자 설정을 사용한다. 임시 경로를 유지한 셸에서
`uv run python -m scripts.init_db` 후
`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`을 실행한다.
실기기 접속에 필요한 바인딩·프록시는 [사용 가이드](usage-guide.md)의 환경에 맞춰 정한다.
현재 운영 Compose는 기본적으로 `127.0.0.1:8002`에 바인딩하므로 개발 서버와 구분한다.

E2E는 합성 자료와 Mock을 사용한다. 병렬 실행 시 별도 Compose 프로젝트를 지정한다.

```bash
(
  set -eu
  export COMPOSE_PROJECT_NAME="pointbook-e2e-$$"
  trap 'docker compose -f e2e/compose.yml down --volumes --remove-orphans' EXIT
  docker compose -f e2e/compose.yml up --build --abort-on-container-exit --exit-code-from e2e
)
bash e2e/production-smoke.sh
```

`production-smoke.sh`는 운영 Compose **구성**을 임시 데이터/환경 경로, 별도 프로젝트·포트로
검증한다. 운영 DB에 쓰는 명령으로 오해해 매번 승인을 요청하지 않는다. 변경 후에도 이 격리가
유지되는지는 확인한다. 구버전 Playwright Chromium 자동 검증은 Win7 Chrome 109·Android
실기 검증의 대체가 아니다. BrowserStack 등 외부 서비스를 사용할 때는 승인된 계정·자료 범위만
사용하고, 접근 불가한 실기 수용은 미실행으로 남긴다.

## 운영·이관 명령

다음은 일반 개발 검증 명령이 아니다. 운영 환경에서 실행할 때는 기존 승인 범위와 대상
DB/환경 경로를 확인한다. 실자료 쓰기 전에는 백업과 복원 검증이 필요하다.
현재 절차의 개선 계획은 WP03이며, 아래 명령 목록이 그 개선 완료를 뜻하지 않는다.

| 목적 | 명령 |
|---|---|
| 상태·로그 | `docker compose ps`, `docker compose logs -f app` (민감 로그 공개 금지) |
| 기동·중지·배포 | `scripts/run.sh`, `scripts/stop.sh`, `scripts/deploy.sh` |
| 초기 관리자 생성 | `uv run python -m scripts.init_db` |
| 빈 DB에 기존 요청서 이관 | `uv run python -m scripts.import_excel --file 기존파일.xlsx` |
| 누적 장부 검증·적용 | `uv run python -m scripts.import_ledger --file 누적장부.xlsx --dry-run` / `--apply` |
| 스키마 변경·드리프트 확인 | `uv run alembic upgrade head`, `uv run alembic check` |
| 수동 백업 | `uv run python -m scripts.backup` |

기동 시에도 마이그레이션이 실행된다. 배포·복원은 [README](../README.md)와 실제 스크립트를
대조하며, main 머지만으로 운영 변경·태그·릴리스까지 수행하지 않는다.

## 모델과 skills 선택

- 복잡한 장부 설계·정정·이전 검토에는 Astra와 높은 추론 강도를 사용하고,
  범위가 명확한 변경에는 기본/medium부터 결과를 평가한다.
- 반복적인 작은 수정은 필요하면 Terra/Luna를 선택한다. 모델 가용성은 클라이언트·계정에 따라
  다르므로 실제 선택 가능한 모델을 확인한다. 모든 작업을 최고 강도로 고정할 필요는 없다.
- 독립 코드 탐색·검토는 subagent에 맡길 수 있다. 구현 파일을 나누기 어렵다면 주 에이전트가
  수정하고 검토 에이전트는 읽기만 한다. 최종 판단·결과 통합은 주 에이전트가 담당한다.
- 전용 skills는 저장소 `.agents/skills/`에서 관리한다. 같은 이름을 개인 경로에 복제해
  덮어쓰려 하지 않는다. 각 skill의 설명으로 적용 범위를 판단하고 필요한 본문만 읽는다.
- `openai-docs`는 공식 문서 조회, `workthrough`는 완료 기록에 사용한다.
  마케팅용 Next.js skill을 Jinja2 업무 화면에 적용하지 않는다.
- 사용자 지정 컨텍스트 윈도우와 자동 압축 한계는 유지한다. 이 문서의 권장 모델 선택은
  개인 설정 파일을 자동 변경하라는 지시가 아니다.

## 지침 변경 검증

skill 생성기의 `quick_validate.py`로 frontmatter·이름·미완성 항목을 확인하고 상대 링크를
검사한다. 구조 검증만으로 실제 자동 선택이나 작업 품질이 증명되지는 않는다.
지침을 크게 바꿨다면 다음 요청을 읽기 전용 모의 검토 또는 격리 환경에서 확인한다.

| 요청 | 관찰할 행동 |
|---|---|
| README 문서 링크의 오타만 수정 | 필요한 문서 확인·링크 검증; 기능 계획이나 전체 테스트 신규 작성은 불필요 |
| B01 금액 입력 오류 수정 | #48·대상 WP, 합성 HTTP 재현, 원문 보존, 관련 회귀 검사를 연결 |
| 과거 장부 정정 설계 검토 | 다음 실제 기록 usage·현재 잔액·감사·충돌을 검토하고 실기 미실행은 별도 표시 |

실제 작업에서는 불필요한 질문, 반복 검사, 작업 시간, 누락 수용 조건을 관찰한다.
측정하지 않은 개선율을 쓰지 않는다. AGENTS 변경 후 새 세션에서 적용 지침을 확인하고,
skill 변경이 보이지 않으면 클라이언트를 다시 시작한다. 이를 위해 압축 한계를 바꾸지 않는다.

## 공식 근거

2026-09-07 확인. 아래는 개발 에이전트 설정 근거이며 제품의 Gemini 계약과 구분한다.

- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md): 지침 계층·발견·리뷰 규칙.
- [Skills](https://learn.chatgpt.com/docs/build-skills): 저장소 경로·점진적 로딩·호출 범위.
- [Astra prompting](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices):
  지침 충돌 점검·승인된 작업 지속·검증 강도 조정.
- [Models](https://learn.chatgpt.com/docs/models): 작업별 모델과 추론 강도.
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): 독립 작업 위임·병렬 쓰기 주의.
