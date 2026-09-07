# 개발 에이전트 지침·skills 정비

v1.4.0 착수 전 준비 작업. 기준 main은 `1becc29f6fe6a19dbaf045f032dce64b683cbdfe`.
관련 계획은 [#47](https://github.com/jihoon22-lee/PointBook/issues/47),
명세는 [#48](https://github.com/jihoon22-lee/PointBook/issues/48)이다.
R24/WP10(#58)의 품질 기준 정합성을 일부 보완하지만 B01~B04 기능 수용 완료를 뜻하지 않는다.

## 변경과 결정

- AGENTS를 업무 불변식·작업 범위·검증·리뷰 기준으로 정리하고, 현재 이슈와 새 목표 계약을 연결했다.
  공용계정 누락 예외, 직전 실제 기록, 개별 수정과 정정의 구분을 명시했다.
- 저장소에 `pointbook-change`, `pointbook-ledger-review` skills를 추가했다.
  단순 문구 수정·읽기 전용 요청의 범위를 보존하고 필요한 검증만 선택한다.
- `docs/agent-workflow.md`에 개발 DB/Compose 격리, 운영 명령, 모델 선택과 공식 근거를 모았다.
  README와 아키텍처 문서의 CI 설명도 실제 의존 관계에 맞췄다.
- CI는 `mypy app scripts`를 실행하며 필수 7개 job 모두 success여야 통과한다.
  E2E 파일이 없을 때 해당 단계를 건너뛰던 조건도 제거했다.
- 사용자 지정 컨텍스트 윈도우·자동 압축 한계 및 개인 Codex 설정은 변경하지 않았다.
  제품 AI 제공자, 앱 코드, DB·환경 파일, 운영 배포·태그·릴리스는 변경하지 않았다.

## 실제 검증

운영 `.env`·DB 없는 격리 worktree, `uv sync --locked --group dev --python 3.13`,
Python 3.13.15와 합성 설정·임시 DB를 사용했다. 의존성 lockfile은 변경하지 않았다.

| 검사 | 결과 |
|---|---|
| 두 skill의 `quick_validate.py` | 통과 |
| 지침·skills·개발 안내·README 상대 파일 링크 | 15개 확인 |
| Ruff check / format | 통과, 80개 파일 |
| `mypy app scripts` | 통과, 38개 소스 |
| pytest + coverage | 249 passed, coverage 93.80%, 186 warnings |
| 임시 DB `alembic upgrade head` / `alembic check` | 통과, 드리프트 없음 |
| `pip-audit` | 알려진 취약점 없음; 로컬 pointbook 패키지는 PyPI 미등록으로 감사 제외 |
| Gitleaks 8.30.1 기존 Git 히스토리 | 42개 커밋, 탐지 없음 |
| 실제 CI 게이트 Bash 실행 | 로컬 25개 결과 조합 통과: 전체 성공만 허용, 실패·취소·skipped·누락·초과 차단 |
| 독립 읽기 전용 검토 | 중요한 결함 없음. 개별 상태·팀 수정 규칙의 명시 보완 의견 반영 |
| 독립 검토자의 게이트 실행 | 32개 조합 확인, 빈 결과도 차단 |

세 요청(README 링크 오타, B01 금액 입력 오류, 과거 장부 정정 설계)의 skill 선택·검증·완료
범위를 독립 에이전트가 모의 판단했다. 단순 문구에 전체 구현 절차를 적용하지 않고,
B01은 HTTP 입력 보존 검증, 정정은 다음 실제 기록/현재 잔액/감사/충돌 검토를 선택했다.
이는 새 클라이언트의 자동 호출 실험이나 실제 기능 구현 테스트가 아니다.

## 한계와 최종 증거

- pytest에서 SQLite 연결 ResourceWarning 등을 관측했다. 앱·공용 fixture는 이번에 변경하지
  않았으며 경고 처리·DB 수명주기는 기존 WP04/WP10 범위에서 추적한다.
- Win7 Chrome 109·Android 실기, 운영 자료 대사, 실제 외부 AI 호출은 수행하지 않았다.
  이번 준비 작업의 자동 검증을 해당 수용 항목의 PASS로 기록하지 않는다.
- E2E·격리 운영 Compose smoke와 최종 커밋의 필수 7개 job 결과는 이 변경 PR의 Checks를
  근거로 확인한다. PR의 source SHA·CI run URL과 머지 결과가 최종 검증 대상을 식별한다.
- 사용자 환경의 새 세션 자동 skill 선택, 작업 시간·질문/반복 검사 감소율은 미측정이다.
