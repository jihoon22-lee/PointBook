# PointBook Guidelines

소방서 포인트 충전 요청서(엑셀)를 웹에서 관리하는 서비스. 매달 소방서가 보내는
포인트 충전 요청서(테이블 형식: 순번, 팀, 이름, 계급, 금액, 개인번호, 비고)를
웹에서 관리·조회한다.

## 핵심 도메인 규칙

- 인원 고유값은 **개인번호 + 이름** 조합. DB에 인원·금액·잔액을 저장
- 매달 요청서 수령 시 AI가 사진을 인식해 리스트를 추출하고, DB의 **전체 인원과 대조
  동기화**한다. (요청서에 있는 사람만 처리하는 것이 아님)
  - 요청서에 있음 → 재직 (기존 재직자 유지 / 복귀자는 비재직 → 재직 전환)
  - 요청서에 없고 기존 DB에 재직자로 존재 → **비재직 처리** (타지역 전출로 판단)
  - 요청서에 있고 기존 DB에 없음 → 신규 재직자 추가
- AI 인식 결과는 사람이 검수·수정 후 확정
- 팀 변경은 팀 필드만 갱신. 팀은 팀 마스터로 관리(추가/삭제 가능), 팀별 색상으로 구분 표시
- 매달 요청서 수령 시점에 사용자가 **처리 대상 전체 인원**(재직 유지·복귀자,
  비재직 전환 대상자, 신규 인원 모두)의 이월 잔액을 인원별로 한 번씩 입력
  - 매달 사용한 합계 = 지난 달 기록의 총 잔액 − 이번 달 입력한 이월 잔액
  - 총 잔액 = 이번 달 들어온 금액 + 이번 달 입력한 이월 잔액
- **비재직자의 잔액은 보존**, 복귀 시 이전 잔액을 이어서 계산
- 월간 일괄 처리와 별개로, **개별 인원 단위 수정** 지원 (재직/비재직 전환, 팀 변경,
  금액·잔액 수정)
- **인원 삭제는 없음** — 퇴직은 항상 비재직 처리로만 관리
- 월별 이력 조회와 **대시보드**(월별 사용량·금액/잔액, 전체/개인별 통계) 제공
- 로그인(관리자 인증) 필요
- 기존 엑셀 데이터의 DB 이관 필요 (초기 마이그레이션)

## 기술 스택 (확정)

- 백엔드: **Python(FastAPI + Uvicorn)**, 패키지 관리 **uv** (lockfile로 재현성 보장)
- DB: **SQLite + SQLAlchemy 2.x** (파일 기반, `data/pointbook.db`)
- 프론트: **Jinja2 서버 렌더링 + 바닐라 JS + 반응형 CSS** (빌드 단계 없음)
- 인증: 세션 쿠키 기반 단일 관리자 계정 (werkzeug 해시)
- AI: **VisionProvider 인터페이스 추상화** + 개발용 Mock 구현체. Gemini/GPT-4o 등은
  키 확보 후 플러그인으로 추가 (키는 `.env`로 관리, 커밋 금지)
- 엑셀 이관: openpyxl 스크립트
- 테스트: pytest(단위) + Playwright E2E(구버전 Chromium, Docker)

## 대상 환경 제약 (최우선 고려 사항)

- 개발 및 서버 구동은 **WSL** 환경. 실제 사용은 **Windows 7**과 **Android(갤럭시) 실기기** 위주
- **Windows 7 브라우저**: Chrome 109(마지막 지원 버전) 또는 IE11
  - IE11 지원 시 최신 프레임워크(React 18+, Next.js 12+, Vite 기본 설정)와 최신 JS 문법 사용 불가
  - 가능하면 "Chrome 109 지원 + IE11은 안내 페이지" 기준으로 수렴하는 것을 권장
- **TLS 1.3은 레거시 브라우저에서 불가** → 서버는 TLS 1.2 협상을 유지
- Android(갤럭시)는 모바일 뷰포트 대응 필요
- 접속은 **HTTP 기본**(내부망). 필요 시 HTTPS(TLS 1.2) 전환 가능한 구조 유지

## 테스트 전략 (Win7/IE11은 로컬 재현 불가)

- 개발 중 자동 테스트: Docker 컨테이너의 구버전 Chromium(Playwright)으로 Blink 엔진 기준 검증
- 배포 전 호환성 검증: BrowserStack(실제 Win7 + IE11/Chrome 109 VM) 스모크 테스트
- WSL 개발 서버 접속: Windows 브라우저는 `localhost:<port>`로 접속 가능.
  갤럭시 실기기는 `.wslconfig`의 `networkingMode=mirrored` 또는 `netsh portproxy` +
  방화벽 규칙 필요. Android 에뮬레이터는 `10.0.2.2:<port>`

## Build, Test, and Development Commands

```bash
uv sync --group dev        # 의존성 설치 (uv.lock 기준)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 개발 서버 (WSL)
uv run pytest              # 단위 테스트
uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85  # 커버리지 확인
uv run ruff check .        # 린트
uv run ruff format --check .  # 포맷 검사
uv run mypy app            # 타입 체크
uv run python -m scripts.init_db   # DB 초기화 (관리자 계정 생성)
```

## CI / 머지 워크플로 (GitHub Actions)

- PR 기준 CI job: `lint`(ruff check+format) → `typecheck`(mypy) → `test`
  (pytest + coverage **85% 이상**) → `security`(pip-audit) → `secret-scan`(gitleaks) → `e2e`
  (구버전 Chromium Playwright, `e2e/` 존재 시)
- 각 단계 완료 후: **계획 대비 자체 검토 → PR(한글, 리뷰 가능 상태) → CI 전체 통과
  → squash merge**. `main`에 직접 푸시 금지 (자체 규칙 — GitHub 브랜치 보호는
  private 무료 요금제에서 불가, squash 머지만 저장소 기본값으로 설정됨)
- PR 머지 전 모든 CI job 통과 필수

## 작업 컨벤션

- 작업 위치는 `/mnt/e/projects/PointBook`(Windows 드라이브 마운트)라 파일 IO와 git이 느리다.
  대용량 생성물을 저장소에 커밋하지 않는다
- Conventional Commits(`type(scope): subject`) 사용, `feature/<task-slug>` 브랜치,
  `main`에 직접 푸시 금지. PR은 명시적으로 미완료인 경우 외에는 review 가능한 상태로 생성
- README, PR 설명, 커밋 메시지 등 사용자가 읽는 내용은 **한글 우선**, 기술 식별자와
  명령어는 원문 표기 유지
- `.env`(API 키, 관리자 비밀번호)와 SQLite DB 파일(`data/*.db`)은 커밋 금지
