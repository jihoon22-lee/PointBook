# 월간 검수의 중복 분석 제거

## 문제와 범위

1,000명 × 24개월 합성 자료에서 월간 검수 HTTP 요청이 3초 응답 기준을 넘었다.
명시 검수 POST가 잠금 전 분석, 초안 저장의 쓰기 잠금 안 분석, 리다이렉트 GET의 최신 분석을
연이어 실행했다. 동일한 인원 대조·직전 관측 조회·DB fingerprint를 세 번 계산하는 경로였다.

- 기준 source SHA: `0a21f1b9523c6db7c604518b600a80eeecb77aac`.
- 작업 브랜치: `feature/review-performance`. 입력·이관·이력 수정 병합 후
  main `75e524c09bb0f81989f328bf9d82e61d4e77458d`로 재기반했다.
- R06/R17/R20/R24·S08/S11 → WP02 #50·WP08 #56의 초안·동시성 계약과
  WP01 #49의 규모 검증 기준을 유지하는 별도 성능 수정이다.
  금액 계산, 식별자, 업무 화면, DB 스키마, 운영 자료를 변경하지 않는다.
- PR source SHA와 merge ref의 7개 필수 CI 결과는 최종 PR 본문에서 연결한다.

## 변경

- 초안 저장의 공통 내부 함수가 쓰기 잠금 안에서 계산한 검수 결과도 반환한다.
  명시 검수 경로는 이 결과와 승인 토큰을 함께 저장하여 잠금 전 중복 분석을 제거한다.
- 기존 자동저장 API는 반환형을 유지한다. 제출된 토큰을 현재 분석과 대조할 뿐
  새 토큰으로 바꾸거나 변경된 입력을 자동 승인하지 않는다.
- 성공 시 리다이렉트 GET에서 현재 DB를 다시 검수한다. 저장 실패 시에는 제출된 원문으로
  오류 화면을 새로 계산하고 기존 서버 초안을 보존한다.
- 초안 버전·소유자·수명 확인, SQLite 쓰기 잠금, 확정 직전 재검수와 재전송 처리는 유지한다.
- R25의 릴리스 추적을 위해 앱·버전 검사·README를 v1.7.2로 맞추고 CHANGELOG에
  앞선 8건 수정과 이번 성능 변경을 기록했다. 앞선 작업 기록에도 실제 PR·source·CI를 연결했다.

## 진단과 검증

운영 `.env`·DB 없는 격리 worktree, Python 3.13, Mock과 합성 자료, 명시적인 임시
`DATABASE_PATH`에서 실행한다. PointBook의 규모 검사는 서로 겹치지 않게 실행했으며
다른 프로젝트가 사용하는 CPU 부하는 제어하지 않았다.

선행 cProfile에서 `review_rows()` 3회 합계 2.086초, 직전 관측 0.688초,
fingerprint 0.415초, 프로필 차이 준비 0.401초, 검수 화면 생성 약 0.991초를 확인했다.
이 값은 프로파일러 비용이 포함되어 시간 개선율 계산에 사용하지 않는다.

동일 조건의 수정 전후 측정 명령:

```bash
DATABASE_PATH=/tmp/<isolated-check>/check.db APP_ENV=test AI_PROVIDER=mock \
  COVERAGE_FILE=/tmp/<isolated-check>/scale.coverage \
  uv run coverage run -m pytest -s tests/test_workflow_scale.py
```

커버리지 계측을 켠 규모 검사만 실행한다. 프로젝트의 전체 coverage ≥ 85% 기준은
변경하지 않으며, 전체 커버리지 판정은 통합 전체 테스트에서 별도로 수행한다.

| 기준 코드 | 규모 | 검수 | 자동저장 | 대시보드 | Excel |
| --- | --- | --- | --- | --- | --- |
| 수정 전 base | 200명 × 24개월 | 1.203초 / 45쿼리 | 0.289초 / 16쿼리 | 1.026초 / 9쿼리 | 0.479초 / 6쿼리 |
| 수정 전 base | 1,000명 × 24개월 | 4.138초, 3초 기준 실패 | 검수 실패로 미실행 | 미실행 | 미실행 |
| 수정 후, 다른 테스트 동시 실행 확인 | 200명 × 24개월 | 3.578초, 3초 기준 실패 | 검수 실패로 미실행 | 미실행 | 미실행 |
| 수정 후, 다른 테스트 동시 실행 확인 | 1,000명 × 24개월 | 7.143초, 3초 기준 실패 | 검수 실패로 미실행 | 미실행 | 미실행 |

수정 전 1,000명 검수의 60쿼리 이하 검사는 통과했다. 정확한 쿼리 수는 시간 기준
assertion 이후에 출력되는 기존 테스트 구조 때문에 실패 로그에 남지 않았다.
규모·3초/5초 응답·쿼리 한도와 85% 전체 커버리지 기준을 완화하지 않는다.

수정 후 첫 측정 중에는 다른 프로젝트의 pytest 프로세스 세 개가 동시에 CPU를 사용하고
있었다. 다른 작업의 프로세스는 변경하지 않았다. 위 측정은 부하 조건이 달라 시간 개선율이나
성능 수용의 근거로 비교하지 않는다.

그 뒤 같은 Python 3.13.15·lockfile·커버리지 계측으로 수정 전/후 규모 검사를 연속 실행했다.
이때도 호스트 부하가 남아 있었으므로 시간 개선율은 주장하지 않는다.

| 코드 | 200명 검수 | 1,000명 검수 | 결과 |
| --- | --- | --- | --- |
| 수정 전 `0a21f1b` | 1.573초 / 45쿼리 | 5.505초 | 1통과·1시간 실패 |
| 수정 후 `8178d06` | 1.129초 / 32쿼리 | 5.058초 | 1통과·1시간 실패 |

200명 기준 조회는 45회에서 32회로 줄었다. 자동저장 16회·대시보드 9회·Excel 6회는 같다.
로컬 1,000명 시간 검사를 통과로 기록하지 않으며, 최종 성능 수용은 같은 기존 시간·쿼리
기준을 실행하는 PR merge ref CI에서 판정한다. 실제 CI 결과는 최종 PR 본문에 연결한다.

- 신규 HTTP 경계 검사 5건 통과: 명시 검수/자동저장 토큰 정책, 쓰기 잠금 직전 프로필 변경,
  저장 후 리다이렉트 GET 전 DB 변경, commit 실패의 원문·기존 초안 보존, 오래된 탭의 명시 검수 충돌.
- Ruff check/format과 mypy `app scripts`(63개 소스) 통과.
- 기존 월간·검수·초안·비재직 이월·입력 경계·동기화·금액 회귀 235건 통과(205.12초).
  `tests/test_drafts.py tests/test_review_workflow.py tests/test_monthly*.py
  tests/test_absent_carry_identity.py tests/test_review_input_fixes.py tests/test_sync.py
  tests/test_carry_inputs.py`를 실행했다.
- 독립 코드 검토에서 명시 검수와 자동저장 토큰 정책, 실패 rollback과 원문 보존,
  리다이렉트 GET 및 확정의 최신 DB 재검증을 확인했다. 추가 차단 결함은 발견하지 못했다.
- 모든 수정과 v1.7.2를 합친 `8178d06e64643713eaf64b3f39b5d86e9c23e8c0`에서
  `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85`를 실행했다.
  **1,068 passed, 1 failed / coverage 94.71%**. 유일한 실패는 1,000명 검수의
  시간 5.431초이며 기능·쿼리 수와 2,000명 보고서의 5초 기준은 통과했다.
- 최종 main 재기반 후 위 source와 앱·테스트·스크립트·일반 문서·빌드/E2E 설정의
  변경이 없음을 대조했다. `tests/test_review_performance.py tests/test_version.py`를
  다시 실행해 **8 passed**를 확인했다. 버전 변경 후 Ruff check/format(189개 파일)도 통과했다.

## v1.7.2 수정과 검증 연결

| 범위 | PR | source | 필수 CI |
| --- | --- | --- | --- |
| F1/F6/F7/F8 입력·검수 | [#93](https://github.com/jihoon22-lee/PointBook/pull/93) | `0a21f1b` | [7개·Quality gate 성공, 1,044개 / 94.61%](https://github.com/jihoon22-lee/PointBook/actions/runs/39936911076) |
| F2/F3/F4 최초 이관 | [#94](https://github.com/jihoon22-lee/PointBook/pull/94) | `2edb07a` | [7개·Quality gate 성공, 1,058개 / 94.69%](https://github.com/jihoon22-lee/PointBook/actions/runs/39938605399) |
| F5 변경 이력 | [#95](https://github.com/jihoon22-lee/PointBook/pull/95) | `25aed18` | [7개·Quality gate 성공, 1,064개 / 94.66%](https://github.com/jihoon22-lee/PointBook/actions/runs/39939897946) |

각 PR의 merge ref와 검토한 source 트리가 같음을 확인하고 squash merge했다.
이번 성능·릴리스 PR도 최종 source와 merge ref CI를 대조한 뒤 병합하고,
검증된 최종 main에 v1.7.2 태그·GitHub Release를 연결한다. 운영 배포와 기존 자료 복구는 별도다.

## 한계와 통합

공유 호스트의 부하를 통제한 로컬 시간 비교는 확보하지 못했다. 시간 기준을 올리거나
규모 검사를 제외하지 않았으며 PR·최종 main CI의 실제 결과와 로컬 결과를 구분한다.
Win7 Chrome 109·갤럭시 실기 및 운영 자료 대사는 수행하지 않았다.
운영 환경 파일·DB·컨테이너·배포 상태는 변경하지 않았다.
