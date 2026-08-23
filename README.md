# PointBook

소방서 포인트 충전 요청서(엑셀)를 웹에서 관리하는 서비스. 매달 소방서가 보내는
포인트 충전 요청서(테이블 형식: 순번, 팀, 이름, 계급, 금액, 개인번호, 포인트번호, 비고)를
웹에서 관리·조회한다.

## 문서

- [아키텍처 문서](docs/architecture.md) — 기술 스택, 모듈 구조, 데이터 모델, 핵심 로직·흐름 다이어그램
- [사용 가이드](docs/usage-guide.md) — 매달 요청서 처리 업무 흐름, 실기기 접속, 실사용 체크리스트
- [변경 이력](CHANGELOG.md) — 버전별 변경 사항

## 주요 기능

- **월간 요청서 처리**: 사진 업로드 → AI 테이블 인식 → 검수·수정 → 확정 시
  DB 전체 인원과 대조 동기화 (재직 유지/복귀, 비재직 전환, 신규 추가, 팀 변경)
- **잔액 계산**: 매달 처리 대상 전체 인원의 이월 잔액 입력 → 부호 있는 순사용·총 잔액 자동 계산
  (비재직자의 잔액은 보존되어 복귀 시 이어짐)
- **인원·팀 관리**: 8자리 포인트번호를 고유 식별자로 사용하고, 인원·팀원의 현재 총잔액과
  팀별 재직·비재직 현황을 확인하며 각 정보 열을 눌러 정렬
- **팀 색상**: 팀 추가 시 자유 색상 팔레트에서 구분 색상을 선택하고, 기존 팀도 팀 목록에서 색상만 안전하게 변경
- **대시보드**: 월별 사용량·금액/잔액 통계 (전체/팀별/개인별), 월별 이력 조회
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
- 기본 접속은 HTTP(내부망). HTTPS 필요 시 uvicorn에 인증서 옵션을 추가해 TLS 1.2로 전환:
  `uv run uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem`

### 갤럭시 실기기·Win7 접속

운영 컨테이너는 보안을 위해 `127.0.0.1:8002`에만 바인딩한다. 다른 기기에서는
Tailscale serve로 이 주소를 프록시한 뒤 tailnet URL로 접속한다.

```bash
tailscale serve --bg https+8002 http://127.0.0.1:8002
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
기존 1.0.x DB는 최초 1회 자동으로 기준점(stamp)이 잡힌다. 수동 명령:

```bash
uv run alembic upgrade head   # 최신 스키마로 마이그레이션
uv run alembic check          # 모델-마이그레이션 드리프트 확인
uv run python -m scripts.backup   # DB 수동 백업 (data/backups/)
```

- 월간 확정 시 `data/backups/`에 DB가 자동 백업된다 (보관 개수 `BACKUP_KEEP`, 기본 30)
- 복구: `scripts/stop.sh`로 컨테이너를 중지한 뒤 원하는 백업 파일을
  `data/pointbook.db`로 복사하고 `scripts/run.sh` 실행

## CI

PR 생성 시 GitHub Actions가 lint → typecheck → test(coverage 85%) → security(pip-audit)
→ secret-scan(gitleaks) → e2e를 실행하고, 전체 통과 후 squash merge 한다.
