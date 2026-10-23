# 월간 변경 이력의 인원 연결 보존

## 문제와 범위

v1.7.1(`91c37f10d81f11cde96f167182d9260b4f1bf25e`) 상세 코드 리뷰 F5의 후속 수정이다.
월간 변경 이력의 처리 유형별 명단이 당시 포인트번호로 현재 인원 마스터를 조회했다.
원래 인원의 번호를 바꾸면 현재 이름 대신 당시 번호가 표시되고, 그 번호를 다른 인원이
사용하면 과거 월간 작업에 다른 사람이 참여한 것처럼 표시됐다.

R14/R16/R23·S04 → [요구사항 #48](https://github.com/jihoon22-lee/PointBook/issues/48) →
WP06 #54·WP07 #55·WP09 #57 후속 결함 F5 → v1.7.2 PR3 범위다.
기준 source는 위 SHA이며 최종 PR·source 및 merge ref CI는 통합 담당이 PR에 연결한다.

## 변경

- 해당 월간 작업이 만든 최초 `BalanceRevision`의 작업 ID·인원 ID로 현재 이름을 조회한다.
  인원 유형으로 제외하지 않으므로 일반 인원과 공용계정에 같은 규칙을 적용한다.
- 인원 연결과 이름을 한 번의 JOIN 조회로 가져온다. 현재 포인트번호로 재연결하지 않으며,
  이후 정정 revision이나 같은 월에 추가된 다른 작업의 기록은 연결 근거로 사용하지 않는다.
- 최초 기록과 연결할 수 없으면 당시 포인트번호를 표시한다. 감사 원문·최초 revision·장부
  금액·인원 정보는 쓰지 않는다. 화면의 설명도 이 표시 규칙에 맞췄다.
- 기존 사용성 검사의 합성 월간 작업에는 실제 최초 기록 연결을 추가했다. 처리 유형별
  순서·인원 수·연결 없는 번호 표시의 기존 검증 의도는 유지했다.

## 실제 검증

운영 `.env`와 DB가 없는 전용 worktree, Python 3.13, Mock, 합성 관리자와 합성 자료를 사용했다.
DB를 사용하는 명령은 매 실행마다 별도의 `/tmp/pointbook-audit-check.*` 경로를 만든 뒤 다음
환경으로 실행했다. 테스트 fixture의 DB도 각 테스트의 임시 디렉터리에 생성된다.

```bash
test ! -e .env
POINTBOOK_AUDIT_CHECK_ROOT="$(mktemp -d /tmp/pointbook-audit-check.XXXXXX)"
export DATABASE_PATH="$POINTBOOK_AUDIT_CHECK_ROOT/check.db"
export AI_PROVIDER=mock APP_ENV=test
export ADMIN_USERNAME=check-admin ADMIN_PASSWORD=check-password SECRET_KEY=check-secret-key
```

- 수정 전 정상 기대값의 핵심 HTTP 회귀 4개가 실패했다. 일반·공용의 번호 변경/재사용과
  최초 연결 없는 옛 작업/후속 과거 삽입에서 다른 현재 번호 소유자를 표시하는 실패를 확인했다.
- `uv run --no-sync pytest tests/test_audit_presentation.py tests/test_monthly_action_identity.py
  tests/test_history_reports.py tests/test_ledger_corrections.py tests/test_profiles.py
  tests/test_ui_usability.py`: **96 passed, 1 failed**. 실패는 최초 기록 없이 현재 번호만으로
  이름을 기대하던 기존 사용성 fixture였으며, 연결 자료 보완 후 해당 검사 **1 passed**를 확인했다.
  시간 제한 실패는 없었다.
- 감사 원본을 새로 조회하여 불변성을 비교하도록 회귀 검사를 강화한 뒤
  `uv run --no-sync pytest tests/test_audit_presentation.py`: **13 passed**.
  이름 변경·번호 변경·번호 재사용·금액 정정 후 revision·과거 삽입·연결 없는 옛 작업을 검증했다.
  25명의 명단 조회에서도 전체 HTTP 처리의 SQL 수가 6회 이하인 것을 확인했다.
- 변경 Python 파일의 Ruff check/format 통과. `uv run --no-sync mypy app scripts`:
  **Success: no issues found in 63 source files**.
- `git diff --check` 통과. 전체 서버·커버리지·E2E·PR 필수 CI는 통합 담당의 최종 검증 범위다.
- 독립 검토에서 최초 작업의 인원 연결·일반/공용·정정/과거 삽입·근거 없는 기존 기록·배치
  조회를 대조했고 추가 차단 결함은 없었다. 최종 source·필수 CI는 PR 및 릴리스 기록에 연결한다.
- 입력·이관 수정을 합친 뒤 `tests/test_audit_presentation.py
  tests/test_monthly_action_identity.py tests/test_ui_usability.py` → **26 passed**.
- PR #94 병합 후 main `a25375668ef0a872fab2e78a8177a5d46bb43c92`로 재기반하고
  앞서 검증한 트리와 동일함을 확인했다. `tests/test_audit_presentation.py`를 다시 실행해
  **13 passed**를 확인했다. 변경과 새 커밋의 Gitleaks 검사에서 민감정보가 검출되지 않았다.

## 최종 PR 검증

[PR #95](https://github.com/jihoon22-lee/PointBook/pull/95), source
`25aed185f3b255140d0bb043450229784849c6ac`와 merge ref
`f2f2d269b1b37d5f81257aa287a135b154b9d9e0`의 트리가 같음을 확인했다.
[CI](https://github.com/jihoon22-lee/PointBook/actions/runs/39939897946)의 필수 7개와
Quality gate가 모두 성공했다. 전체 서버 **1,064개 / coverage 94.66%**, E2E·복구 smoke 성공.
Squash merge: `75e524c09bb0f81989f328bf9d82e61d4e77458d`.

## 한계

실제 운영 DB 대사와 Win7 Chrome 109·갤럭시 실기는 실행하지 않았다. HTTP 템플릿 검증을
실기 수용으로 기록하지 않는다. 기존 자료를 자동 정정하거나 운영에 배포하는 작업은 포함하지 않는다.
