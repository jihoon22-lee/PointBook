# PointBook

소방서 포인트 충전 요청서(엑셀)를 웹에서 관리하는 서비스. 매달 소방서가 보내는
포인트 충전 요청서(테이블 형식: 순번, 팀, 이름, 계급, 금액, 개인번호, 비고)를
웹에서 관리·조회한다.

## 주요 기능

- **월간 요청서 처리**: 사진 업로드 → AI 테이블 인식 → 검수·수정 → 확정 시
  DB 전체 인원과 대조 동기화 (재직 유지/복귀, 비재직 전환, 신규 추가, 팀 변경)
- **잔액 계산**: 매달 처리 대상 전체 인원의 이월 잔액 입력 → 사용 합계·총 잔액 자동 계산
  (비재직자의 잔액은 보존되어 복귀 시 이어짐)
- **인원·팀 관리**: 개인번호+이름 고유, 개별 수정(상태/팀/금액), 팀 마스터(색상 구분)
- **대시보드**: 월별 사용량·금액/잔액 통계 (전체/팀별/개인별), 월별 이력 조회

## 기술 스택

Python FastAPI + SQLite(SQLAlchemy 2.x) + Jinja2/바닐라 JS + 세션 인증.
AI는 VisionProvider 인터페이스로 추상화되어 있으며 개발 중에는 Mock 구현체를 사용한다.

## 개발 환경 (WSL)

```bash
uv sync --group dev                # 의존성 설치
cp .env.example .env               # 관리자 계정/비밀번호 설정
uv run python -m scripts.init_db   # DB 초기화 (관리자 계정 생성)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 서버 실행
```

접속: Windows(호스트) 브라우저는 `http://localhost:8000` 으로 바로 접속 가능.

### 갤럭시 실기기 접속 (WSL2)

- `.wslconfig`에 `networkingMode=mirrored` 설정, 또는
- `netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=<WSL IP>`
  + 방화벽 인바운드 8000 포트 허용

## 테스트

```bash
uv run pytest                    # 단위 테스트
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

E2E(구버전 Chromium)는 `e2e/` 참고 — Docker 컨테이너에서 실행한다.

## 엑셀 이관

기존 엑셀 데이터를 DB로 옮길 때:

```bash
uv run python -m scripts.import_excel --file 기존파일.xlsx
```

## CI

PR 생성 시 GitHub Actions가 lint → typecheck → test(coverage 85%) → security(pip-audit)
→ secret-scan(gitleaks) → e2e를 실행하고, 전체 통과 후 squash merge 한다.
