# 이후 월 관측 차단을 확정 대상 계정으로 한정

## 문제와 확정 범위

#85(v1.7.0 포함, v1.6.1에는 없음)의 리뷰 지적
([discussion_r4307488607](https://github.com/jihoon22-lee/PointBook/pull/85#discussion_r4307488607))이
main(`ccfb42d`)까지 남아 있었다.

- 검수 단계 `new_month_error()`가 처리 월보다 늦은 `BalanceAdjustment`를 모든 계정에서 찾았다.
  요청서에 없는 공용계정 하나에 이후 월 보정·신규 등록이 있으면 관계없는 이전 월 검수 전체가 막혔다.
- 요청서에 없는 공용계정은 `analyze()`가 변경 대상에서 제외하므로 확정해도 기록·잔액·상태가 바뀌지 않는다.
  확정 단계의 잠금 검사(`app/routers/monthly.py`)는 이미 `analysis.changes`의 계정만 본다.

R04/R16 → [요구사항 #48](https://github.com/jihoon22-lee/PointBook/issues/48) 후속 수정 → 본 PR.

## 변경

- 검수 단계 검사 대상을 확정이 기록을 쓰는 계정으로 한정했다: 모든 일반 인원(요청서에 없으면
  비재직 전환·기존 비재직 유지로 기록됨)과 요청서 행의 포인트번호에 해당하는 공용계정.
  `new_month_error()`는 행 검증과 `analyze()` 이전에 호출되므로 요청서의 정규화된 포인트번호를 받는다.
- 확정 단계 잠금 검사·차단 문구·DB 스키마·잔액 계산은 변경하지 않았다.

## 실제 검사

운영 환경 파일·DB 없는 worktree, Python 3.13, 명시적인 임시 `DATABASE_PATH`, Mock/합성 자료를 사용했다.

- `tests/test_observation_order.py`에 두 검사를 추가했다.
  - 요청서에 없는 공용계정의 이후 월 보정이 있어도 이전 월 검수·확정이 통과하고, 공용계정에는
    기록이 생기지 않으며 잔액·상태가 그대로다. 수정 전 코드에서는 실패했다.
  - 요청서에 포함된 공용계정의 이후 월 보정은 여전히 차단된다.
  - 기존 검사(요청서에 없는 일반 인원의 이후 월 등록 차단, 요청서 인원 보정 차단, 확정 단계 409)는 계속 통과한다.
- 전체 서버 검사 1010 passed, coverage 94.55%. Ruff check/format, mypy app scripts,
  Alembic upgrade/check 통과.
- 화면·JS 변경이 없어 구버전 Chromium E2E는 로컬에서 실행하지 않고 PR CI의 `e2e`로 확인한다.

## 한계

- 실환경 수용은 하지 않았다. 운영 반영은 별도 배포 승인 대상이다.
