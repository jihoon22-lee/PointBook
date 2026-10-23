# v1.7.2 검토 후속 — 최초 이관 자료와 잔액 관측 보호

## 문제와 범위

- 전체 코드 리뷰 F2/F3/F4를 구현한다. 기존 요청서 한 달 이관에서 비고가 빠지고 일반 인원의
  충전액이 0원이어도 현재 상태를 재직으로 만들었다. 누적 장부 이관은 월별 이력만 확인해
  개별 등록·현재 보정의 실제 관측이 있는 계정의 현재 잔액을 과거 자료로 덮을 수 있었다.
- 기준 source SHA: `91c37f10d81f11cde96f167182d9260b4f1bf25e` (`v1.7.1`).
- 추적: R05/R14/R18/R22/R23, S05/S10 → 기존 Excel 이관 F2/F3;
  R01/R04/R16/R18/R19, S03/S05 → 누적 이관 F4 → 이번 PR.
  [요구사항 #48](https://github.com/jihoon22-lee/PointBook/issues/48)의 관측·이력 보존 계약을 따른다.
  과거 WP의 전체 완료를 새로 주장하지 않는다. PR·최종 검증 SHA·CI는 게시 후 연결한다.

## 변경

- 비고 셀만 공백을 제거하지 않고 CRLF/CR을 LF로 바꿔 저장한다. 앞뒤·중간 빈 줄과 줄 내부
  공백을 기록·최초 revision·월간 및 잔액 보고서까지 보존한다. 이름·팀 등 기존 셀 정리는 유지한다.
- 빈 DB에 요청서를 이관할 때 일반 인원의 현재 상태도 해당 월 지급액 기준으로 초기화한다.
  0원 충전은 비재직, 양수 충전은 재직이며 이월 잔액은 보존한다.
- 누적 장부의 분석과 적용에서 월별 이력 및 `BalanceAdjustment` 존재를 함께 확인한다.
  초기 0원 관측도 적용 거부 대상이다. 관측 없는 기존 인원 번호 매칭은 계속 지원한다.
- CLI가 `BEGIN IMMEDIATE`로 분석부터 적용까지 쓰기 잠금을 소유한다. 서비스는 적용 직전
  관측을 재확인하고 호출자 transaction에 적용한다. 서비스 안에서 transaction을 새로 시작하지
  않으며 기존 적용 실패의 전체 rollback 동작은 유지한다.

## 합성 회귀 검증

- 수정 전 새 회귀 14사례 중 13사례 실패, 관측 없는 번호 매칭 대조군 1사례 통과를 확인했다.
  실패는 비고 소실, 0원 인원의 active 상태, 분석/적용/CLI의 관측 누락, 쓰기 잠금 부재였다.
- LF·CRLF·CR 비고의 앞뒤·중간 빈 줄과 공백을 DB·최초 revision·보고서·Excel 재읽기까지 대조했다.
- 이번 달 0원/양수 이관의 현재 상태와 월별 상태를 대조하고 다음 요청서 누락의
  `inactive_kept`/`deactivated` 분류를 확인했다.
- 분석 뒤 다른 DB session이 0원 또는 500,000원 관측을 commit하면 적용을 거부하고 모든
  비즈니스 테이블이 그대로임을 확인했다. CLI dry-run/apply에도 같은 거부·무변경 검사를 적용했다.
- CLI 분석과 적용 중 별도 SQLite 연결의 쓰기 잠금 시도가 모두 거부됨을 확인했다.
  관측 없는 기존 포인트번호 매칭, 기존 legacy 번호 매칭, 월별 이력 중복 거부와 사후 실패 rollback도 확인했다.

## 환경·명령·결과

- 격리 worktree `/tmp/pointbook-v172.ZfAryL/import`, Python 3.13.15, `uv.lock` 사용.
  운영 `.env`·DB를 열거나 복사하지 않았다. 모든 DB 검사는 임시 `DATABASE_PATH`,
  `AI_PROVIDER=mock`, 합성 관리자·서명키 설정으로 실행했다.
- 회귀 재현: `uv run pytest tests/test_excel_import.py tests/test_ledger_import.py
  tests/test_import_ledger_cli.py -k 'preserves_note or zero_payment or observation or competing_writes' -q`
  → 수정 전 13실패/1통과.
- 관련 검증: `uv run pytest tests/test_excel_import.py tests/test_ledger_import.py
  tests/test_import_ledger_cli.py tests/test_legacy_status.py tests/test_input_boundaries.py -q`
  → 80사례 통과. 최초 수정 후 보고서 테스트의 필수 정렬 인자 누락 3건은 테스트 호출을 수정했다.
- `uv run mypy app scripts` → 63개 소스 통과.
- 비고 셀에만 LF 정규화를 한정한 뒤 `uv run pytest -o addopts=''
  tests/test_excel_import.py tests/test_import_ledger_cli.py -q` → 19 passed.
- `uv run ruff check .`, `uv run ruff format --check .` → 통과, 186개 파일 형식 일치.
- `git diff --check` → 통과. 독립 검토에서 비고·상태·실제 0원 관측·잠금·부분 변경 방어를
  대조했고 추가 차단 결함은 없었다. 최종 source와 통합 CI는 PR에 연결한다.
- 입력 수정 PR #93 병합 후 `9bc1b512d8996c98a7980057005df8d4e0a8de45`로 재기반했다.
  문서의 두 변경이 함께 유지됨을 확인하고 위 관련 5개 파일의 80사례를 다시 실행해 통과했다.
  변경 파일과 새 커밋의 Gitleaks 검사에서도 민감정보가 검출되지 않았다.

## 한계

- 전체 pytest/coverage·필수 CI·E2E·성능의 최종 결과는 PR 및 릴리스 기록에 연결한다.
- 운영 이관, 실자료 대사, Win7 Chrome 109·Android 실기 검사는 수행하지 않았다.
- 이미 이관되어 사라진 비고나 잘못 설정된 현재 상태를 자동 추정해 복구하지 않는다.
