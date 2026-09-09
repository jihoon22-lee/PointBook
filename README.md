# PointBook

소방서 포인트 충전 요청서(엑셀)를 웹에서 관리하는 서비스. 매달 소방서가 보내는
팀·이름·충전액 등이 담긴 포인트 충전 요청서를
웹에서 관리·조회한다.

## 문서

- [아키텍처 문서](docs/architecture.md) — 기술 스택, 모듈 구조, 데이터 모델, 핵심 로직·흐름 다이어그램
- [사용 가이드](docs/usage-guide.md) — 매달 요청서 처리 업무 흐름, 실기기 접속, 실사용 체크리스트
- [변경 이력](CHANGELOG.md) — 버전별 변경 사항
- [개발 에이전트 작업 안내](docs/agent-workflow.md) — 지침·skills, 격리 검증, 운영 명령 구분

현재 버전: **v1.5.0**

## 주요 기능

- **월간 요청서 처리**: 사진 업로드 → AI 테이블 인식 → 검수·수정 → 확정 시
  DB 전체 인원과 대조 동기화 (재직 유지/복귀, 비재직 전환, 신규 추가, 팀 변경)
- **잔액 계산**: 매달 처리 대상 전체 인원의 이월 잔액 입력 → 부호 있는 순사용·총 잔액 자동 계산
  (기존 비재직자도 매월 별도 구역에서 잔액 입력, 복귀 시 직전 실제 기록에서 이어 계산)
- **인원·팀 관리**: 8자리 포인트번호를 고유 식별자로 사용하고, 인원·팀원의 현재 총잔액과
  팀별 재직·비재직 인원 및 총 잔액 합계, 장부 기준 재직 기간을 확인하고 기존 정보 열을 눌러 정렬
- **팀 색상**: 팀 추가 시 자유 색상 팔레트에서 구분 색상을 선택하고, 기존 팀은 색상 선택 완료 시 자동 저장
- **정정·감사**: 기본 정보·과거 장부 정정·현재 잔액 보정을 분리하고 전후 검토·사유·불변 이력을 보존
- **대시보드**: 월별 재직·비재직 전환 인원과 충전·순사용·총 잔액을 일반/공용·팀·개인별 조회하며 현재 인원 정보 사용
- **입력 편의**: 월간 열 제목 고정, 이월 잔액·충전액 색상 구분, 금액 자동 쉼표와 확정 실패 원인 안내
- **팀별 통계**: 재직자 표 아래에 비재직자의 마지막 확인 잔액까지 포함한 전체 표 제공, Excel 동일 기준
- **서버 초안**: 자동 저장·새로고침/다른 기기 복구·두 탭 충돌 방지·잔액 집중 입력
- **번호 없는 요청서**: 표준 Excel v2 또는 제목 포함 붙여넣기 → 이름·개인번호·유형이 모두 일치하는
  기존 계정이 하나면 자동 연결. 여러 후보는 직접 선택하며 연결 결과를 수동으로 바꿀 수 있음.
  현재 정보와 이번 입력이 다르면 해당 칸 아래에서 위아래로 비교해 항목별 선택하고 확정 때 반영.
  포인트번호는 외부 시스템 발급값만 사용하며 신규 미발급 인원은 초안에서 대기
- **표준 Excel**: 포인트번호 없는 v2 입력 양식 다운로드(기존 v1 업로드 호환)→검수·초안→확정, 화면과 같은 조회 조건의 보고서 다운로드 및 여러 줄 비고 보존
- **자동 백업**: 월간 확정 전 DB 자동 백업 (`data/backups/`), 보관 개수 제한
- **설정**: 관리자 비밀번호 변경 (`/settings`)

## 기술 스택

Python FastAPI + SQLite(SQLAlchemy 2.x) + Alembic(마이그레이션) + Jinja2/바닐라 JS + 세션 인증.
운영 서버는 Docker Compose 단일 앱 컨테이너로 실행하고 기존 `data/`를 bind mount한다.
AI는 VisionProvider 인터페이스로 추상화 — Gemini(사진 테이블 인식)와 개발용 Mock 구현체를 제공한다.

## 개발 환경 (WSL)

```bash
uv sync --group dev                # 의존성 설치
cp .env.example .env               # 관리자 계정/비밀번호 설정
uv run python -m scripts.init_db   # DB 초기화 (관리자 계정 생성)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 서버 실행 (개발)
```

## 실행 · 배포 (WSL 상시 구동)

```bash
cp .env.example .env           # ADMIN_USERNAME / ADMIN_PASSWORD / SECRET_KEY 설정
scripts/run.sh                 # 이미지 빌드 + Docker Compose 상시 실행
docker compose ps              # 상태·health 확인
docker compose logs -f app     # 서버 로그
scripts/stop.sh                # PointBook 컨테이너 중지
scripts/deploy.sh              # main 최신화·이미지 빌드·DB 백업·재시작
```

- 기존 `data/pointbook.db`와 `data/backups/`는 컨테이너 `/app/data`에 연결되므로
  이미지나 컨테이너를 교체해도 그대로 유지된다.
- 접속: **Windows(호스트) 브라우저**는 `http://localhost:8002`로 접속한다.
- 운영 포트 변경: `POINTBOOK_PORT=8001 scripts/run.sh`
- 기본 접속은 HTTP(내부망). HTTPS가 필요하면 인증서·프록시를 구성하고 대상 기기의 TLS 1.2 협상을 검증한다. 독립 개발 서버 예시:
  `uv run uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem`

### 갤럭시 실기기·Win7 접속

운영 컨테이너는 보안을 위해 `127.0.0.1:8002`에만 바인딩한다. 다른 기기에는
승인된 LAN/프록시 또는 tailnet 접속 경로가 필요하다. Tailscale Serve를 사용하도록 구성한 환경의 예시:

```bash
tailscale serve --bg --https=8002 http://127.0.0.1:8002
tailscale serve status
```

Android 에뮬레이터나 개발용 LAN 직접 노출이 필요할 때는 운영 Compose 대신 개발 서버를
별도 포트·바인딩으로 실행한다.

## 테스트

```bash
uv run pytest                    # 단위 테스트
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85
uv run ruff check .
uv run ruff format --check .
uv run mypy app scripts
```

E2E(구버전 Chromium ~Chrome 110, Docker):

```bash
docker compose -f e2e/compose.yml up --build --abort-on-container-exit --exit-code-from e2e
bash e2e/production-smoke.sh       # 운영 Compose health·SQLite 영속성
```

## 엑셀 이관

기존 엑셀 요청서 데이터를 빈 DB로 옮길 때:

```bash
uv run python -m scripts.import_excel --file 기존파일.xlsx [--month 2026-07]
```

고정 형식의 누적 장부는 먼저 dry-run 결과를 확인하고 적용한다. 원본 `.xlsx`와 실제
DB·백업은 `data/` 등 gitignore 경로에만 두며 커밋하지 않는다.

```bash
uv run python -m scripts.import_ledger --file 누적장부.xlsx --dry-run
uv run python -m scripts.import_ledger --file 누적장부.xlsx --apply
```

- dry-run은 실제 DB 대신 임시 복사본에 스키마 마이그레이션을 적용하므로 원본 DB를 바꾸지 않는다.
- `--apply`는 기존 DB를 먼저 백업하고, 전체 계정·월·기록을 한 트랜잭션으로 저장한다.
- 이력 없는 테스트 계정 정확히 2개를 교체하려면
  `--replace-empty-history-people`를 추가한다. 다른 개수나 일부 매칭 계정이 있으면 중단한다.
- 실패하거나 월별 이력이 이미 존재하면 덮어쓰지 않고 전체 적용을 중단한다.

## DB 마이그레이션·백업

스키마 마이그레이션은 Alembic이 담당하며, 서버 기동 시 자동으로 적용된다.
기존 무버전 DB는 알려진 전체 구조와 일치할 때만 과거 revision으로 판별한다. 수동 명령:

```bash
uv run alembic upgrade head   # 최신 스키마로 마이그레이션
uv run alembic check          # 모델-마이그레이션 드리프트 확인
uv run python -m scripts.backup   # DB 수동 백업 (data/backups/)
```

- 월간 확정 전 SQLite backup API로 일관된 사본과 검증 metadata를 보존한다.
- 정기 백업은 `scripts/scheduled-backup.sh`, 복원은 `scripts/restore.sh 검증사본.db`를 사용한다.
- 선택 환경·별도 백업 경로·복원 rehearsal·실패 복구는 [운영 절차](docs/backup-restore.md)를 따른다.
- 운영은 `APP_ENV=production`으로 설정한다. 기본 비밀키·기본 관리자 DB 암호·Mock AI는 차단한다.
  비밀번호 변경은 기존 세션을 폐기하며 모든 상태 변경 요청에 CSRF 검증을 적용한다.

## CI

PR 생성 시 lint, typecheck(`mypy app scripts`), test(coverage 85%), migrations,
security(pip-audit), secret-scan(gitleaks), e2e를 실행한다. e2e는 lint·typecheck·test 성공 후
실행하며, `Quality gate`는 필수 7개 job이 모두 `success`일 때만 통과한다.
실패·취소·건너뜀을 통과로 취급하지 않으며 전체 통과 후 squash merge 한다.


v1.4.0의 자동 검증·실기·운영 수용 상태는 [수용 기록](docs/v1.4.0-acceptance.md)에서 구분한다.
v1.4.1의 수정 범위와 실제 검증 결과는 [작업 기록](workthrough/2026-09-07-v1.4.1-ledger-fixes.md)에서 추적한다.
표준 파일 계약은 [Excel 안내](docs/excel-workflow.md), 초안·정정 업무는 [사용 가이드](docs/usage-guide.md)를 따른다.
