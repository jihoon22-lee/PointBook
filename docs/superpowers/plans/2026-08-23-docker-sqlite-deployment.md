# Docker Compose + SQLite Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PointBook을 기존 SQLite 데이터 그대로 Docker Compose 단일 컨테이너로 상시 실행하고, 안전한 배포·백업·검증 흐름을 제공한다.

**Architecture:** 운영용 Docker 이미지는 애플리케이션 코드와 `uv.lock` 기반 의존성만 포함하고, 호스트 `data/`와 `.env`는 런타임에 연결한다. Compose의 `restart: unless-stopped`, localhost 전용 포트, healthcheck를 사용하며 배포 스크립트는 이미지 빌드 후 PointBook만 중지하고 DB를 백업한 다음 새 컨테이너를 기동한다.

**Tech Stack:** Docker Engine 29, Docker Compose v5, Python 3.13, uv, FastAPI/Uvicorn, SQLite, pytest, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-08-23-docker-sqlite-deployment-design.md`

## Global Constraints

- 실제 DB `data/pointbook.db`, `.env`, 백업 파일은 이미지·테스트 산출물·Git에 포함하지 않는다.
- PostgreSQL, GHCR, Tailscale, TLS, 방화벽은 변경하지 않는다.
- 운영 포트는 `127.0.0.1:8002`이고 컨테이너 내부 포트는 `8000`이다.
- 기본 데이터 경로는 `./data`, 컨테이너 경로는 `/app/data`다.
- PointBook 이외의 프로세스와 Docker 컨테이너는 중지·재시작하지 않는다.
- 실제 DB 집계 78계정·재직 44·비재직 30·공용 4·28개월·1,392기록을 전환 전후 보존한다.
- 기존 작업 위치를 사용하며 별도 worktree나 프로젝트 디렉터리를 만들지 않는다.

---

### Task 1: 운영 Docker 이미지와 Compose 계약

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Modify: `.dockerignore`
- Create: `tests/test_docker_deployment.py`

**Interfaces:**
- Consumes: `.env`, `${POINTBOOK_DATA_DIR:-./data}`, `${POINTBOOK_PORT:-8002}`
- Produces: Compose 서비스 `app`, 이미지 `pointbook:${POINTBOOK_VERSION:-local}`, `/login` healthcheck

- [ ] **Step 1: Docker 계약의 실패 테스트 작성**

```python
from pathlib import Path

import yaml


def test_production_compose_preserves_sqlite_and_is_session_independent():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]
    assert app["restart"] == "unless-stopped"
    assert app["init"] is True
    assert "127.0.0.1:${POINTBOOK_PORT:-8002}:8000" in app["ports"]
    assert "${POINTBOOK_DATA_DIR:-./data}:/app/data" in app["volumes"]
    assert app["environment"]["DATABASE_PATH"] == "/app/data/pointbook.db"
    assert "healthcheck" in app


def test_docker_context_excludes_secrets_and_real_data():
    ignored = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "data/" in ignored
```

- [ ] **Step 2: 테스트가 Docker 자산 부재로 실패하는지 확인**

Run: `uv run pytest tests/test_docker_deployment.py -q`

Expected: `docker-compose.yml` 또는 `Dockerfile`이 없어 FAIL.

- [ ] **Step 3: 최소 운영 Docker 자산 구현**

`Dockerfile`은 `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`을 기반으로 `uv sync --frozen --no-dev`를 실행한다. `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `/app/.venv/bin` PATH를 설정하고 UID/GID 1000의 비루트 사용자로 실행한다. CMD는 관리자 초기화를 한 뒤 Uvicorn을 `0.0.0.0:8000`으로 `exec`한다.

`docker-compose.yml`의 핵심 계약:

```yaml
name: pointbook
services:
  app:
    build: .
    image: pointbook:${POINTBOOK_VERSION:-local}
    init: true
    restart: unless-stopped
    env_file:
      - path: .env
        required: false
    environment:
      DATABASE_PATH: /app/data/pointbook.db
    volumes:
      - "${POINTBOOK_DATA_DIR:-./data}:/app/data"
    ports:
      - "127.0.0.1:${POINTBOOK_PORT:-8002}:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/login')"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 10s
    stop_grace_period: 30s
```

- [ ] **Step 4: Docker 계약과 Compose 렌더링 검증**

Run: `uv run pytest tests/test_docker_deployment.py -q`

Expected: PASS.

Run: `docker compose config --quiet`

Expected: exit 0.

- [ ] **Step 5: 변경 커밋**

```bash
git add Dockerfile docker-compose.yml .dockerignore tests/test_docker_deployment.py
git commit -m "feat(deploy): 운영 Docker Compose 구성 추가"
```

### Task 2: Docker 실행·중지·배포 스크립트

**Files:**
- Modify: `scripts/run.sh`
- Modify: `scripts/stop.sh`
- Modify: `scripts/deploy.sh`
- Modify: `tests/test_deploy_script.py`

**Interfaces:**
- Consumes: Compose 서비스 `app`, `data/server.pid`, `data/pointbook.db`
- Produces: `scripts/run.sh`의 healthy 대기, `scripts/stop.sh`의 Compose/legacy 정리, 안전한 배포 순서

- [ ] **Step 1: 실행 순서와 범위의 실패 테스트 작성**

```python
def test_deploy_builds_before_stop_and_backs_up_before_compose_start():
    script = Path("scripts/deploy.sh").read_text(encoding="utf-8")
    build = script.index("docker compose build app")
    stop = script.index("scripts/stop.sh")
    backup = script.index("cp data/pointbook.db")
    start = script.index("docker compose up -d app")
    assert build < stop < backup < start


def test_run_waits_for_compose_health():
    script = Path("scripts/run.sh").read_text(encoding="utf-8")
    assert "docker compose up -d" in script
    assert "Health.Status" in script
    assert "unhealthy" in script


def test_stop_targets_only_pointbook_and_legacy_pid():
    script = Path("scripts/stop.sh").read_text(encoding="utf-8")
    assert "docker compose stop app" in script
    assert "data/server.pid" in script
    assert "docker stop" not in script
```

- [ ] **Step 2: 새 테스트가 기존 PID 방식 스크립트에서 실패하는지 확인**

Run: `uv run pytest tests/test_deploy_script.py -q`

Expected: Compose 명령 부재로 FAIL.

- [ ] **Step 3: Compose 기반 스크립트 구현**

`scripts/run.sh`은 `docker compose up -d --build app` 후 `docker compose ps -q app`의 컨테이너 ID를 구하고 `docker inspect --format '{{.State.Health.Status}}'`를 최대 60초 확인한다. `healthy`면 성공하고 `unhealthy`, 컨테이너 종료, 시간 초과는 로그 안내와 함께 실패한다.

`scripts/stop.sh`은 Compose `app` 서비스가 존재하면 `docker compose stop app`만 호출한다. `data/server.pid`가 있으면 PID의 명령줄에 `uvicorn app.main:app`이 포함되고 `/proc/<PID>/cwd`가 현재 저장소와 같은지 각각 확인한 뒤에만 종료한다. 어느 하나라도 확인할 수 없으면 임의로 kill하지 않고 실패한다.

`scripts/deploy.sh`은 새 이미지를 먼저 빌드한 후 stop → backup → `docker compose up -d app` → healthy 대기 → 호스트 `/login` HTTP 200 순서로 수행한다. 성공한 경우에만 완료 메시지를 출력한다.

- [ ] **Step 4: 스크립트 테스트와 정적 문법 검사**

Run: `uv run pytest tests/test_deploy_script.py -q`

Expected: PASS.

Run: `bash -n scripts/run.sh scripts/stop.sh scripts/deploy.sh`

Expected: exit 0.

- [ ] **Step 5: 변경 커밋**

```bash
git add scripts/run.sh scripts/stop.sh scripts/deploy.sh tests/test_deploy_script.py
git commit -m "feat(deploy): Compose 기반 서버 수명 관리"
```

### Task 3: 운영 Compose 스모크 테스트와 CI

**Files:**
- Create: `e2e/production-smoke.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_docker_deployment.py`

**Interfaces:**
- Consumes: `POINTBOOK_DATA_DIR`, `POINTBOOK_PORT`, `COMPOSE_PROJECT_NAME`, 테스트 전용 관리자 설정
- Produces: 임시 SQLite 영속성 및 컨테이너 재시작 검증

- [ ] **Step 1: 스모크 스크립트 계약의 실패 테스트 작성**

```python
def test_production_smoke_uses_isolated_project_data_and_cleanup():
    script = Path("e2e/production-smoke.sh").read_text(encoding="utf-8")
    assert "mktemp -d" in script
    assert "COMPOSE_PROJECT_NAME" in script
    assert "POINTBOOK_DATA_DIR" in script
    assert "POINTBOOK_PORT" in script
    assert "docker compose down" in script
    assert "docker compose restart app" in script
```

- [ ] **Step 2: 스모크 스크립트 부재로 실패하는지 확인**

Run: `uv run pytest tests/test_docker_deployment.py -q`

Expected: `e2e/production-smoke.sh`가 없어 FAIL.

- [ ] **Step 3: 격리된 운영 Compose 스모크 구현**

스크립트는 `mktemp -d`로 데이터 디렉터리를 만들고 임의의 비운영 포트와 고유 Compose 프로젝트명을 설정한다. 테스트 전용 `ADMIN_PASSWORD`, `SECRET_KEY`, `AI_PROVIDER=mock`을 환경 변수로 제공하고 Compose를 시작한다. healthy와 `/login`을 확인한 다음 생성된 SQLite 파일의 표식 데이터를 기록하고 `docker compose restart app` 뒤에도 같은 값이 남는지 확인한다. `trap`은 해당 고유 Compose 프로젝트만 `down`하고 임시 디렉터리를 정리한다.

- [ ] **Step 4: CI e2e job에 운영 스모크 추가**

기존 구버전 Chromium E2E가 성공한 뒤 저장소 루트에서 `bash e2e/production-smoke.sh`를 실행한다. 기존 `Quality gate`의 `e2e` 의존성은 유지되므로 별도 필수 체크 이름을 추가하지 않는다.

- [ ] **Step 5: 로컬 운영 스모크 실행**

Run: `bash e2e/production-smoke.sh`

Expected: 임시 Compose 앱이 healthy, 재시작 후 데이터 영속성 PASS, 실제 `data/`와 8002 미사용.

- [ ] **Step 6: 변경 커밋**

```bash
git add e2e/production-smoke.sh .github/workflows/ci.yml tests/test_docker_deployment.py
git commit -m "test(deploy): 운영 Compose 영속성 검증"
```

### Task 4: 문서와 v1.3.0

**Files:**
- Modify: `README.md`
- Modify: `docs/usage-guide.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`
- Modify: `AGENTS.md`
- Modify: `app/_version.py`
- Modify: `tests/test_version.py`

**Interfaces:**
- Consumes: 확정된 Compose 명령과 로그·백업 경로
- Produces: 사용자 운영 안내와 일치하는 `1.3.0` 버전

- [ ] **Step 1: 버전 실패 테스트 수정 및 확인**

`tests/test_version.py`의 명시적 버전을 `1.3.0`으로 먼저 변경한다.

Run: `uv run pytest tests/test_version.py -q`

Expected: 앱 버전 `1.2.3`과 달라 FAIL.

- [ ] **Step 2: 앱 버전과 문서 갱신**

`app/_version.py`를 `1.3.0`으로 올린다. README, 사용 가이드, 아키텍처, AGENTS 명령을 Docker Compose 운영 방식으로 바꾼다. 변경 이력에는 SQLite 파일을 변환 없이 유지하며 Codex 세션과 서버 수명을 분리했다는 점을 명시한다.

- [ ] **Step 3: 문서·버전 테스트 확인**

Run: `uv run pytest tests/test_version.py tests/test_docker_deployment.py tests/test_deploy_script.py -q`

Expected: PASS.

- [ ] **Step 4: 변경 커밋**

```bash
git add README.md docs/usage-guide.md docs/architecture.md CHANGELOG.md AGENTS.md app/_version.py tests/test_version.py
git commit -m "docs(release): v1.3.0 Docker 운영 안내"
```

### Task 5: 전체 검증, PR, 릴리스, 실제 전환

**Files:**
- Verify only; 수정이 필요하면 해당 소유 파일과 테스트를 함께 변경

**Interfaces:**
- Consumes: 완성된 브랜치와 실제 SQLite DB
- Produces: 병합된 PR, `v1.3.0` 릴리스, Docker Compose로 실행 중인 8002 서버

- [ ] **Step 1: 전체 로컬 검증**

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

Run: `uv run mypy app scripts`

Run: `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85`

Run: `uv run alembic upgrade head`

Run: `uv run alembic check`

Run: `uv run pip-audit`

Run: `docker compose config --quiet`

Run: `bash e2e/production-smoke.sh`

Run: `docker compose -f e2e/compose.yml up --build --abort-on-container-exit --exit-code-from e2e`

Expected: 모든 명령 exit 0, pytest coverage 85% 이상, 구버전 Chromium 6개 PASS.

- [ ] **Step 2: 자체 검토와 PR**

`git diff main...HEAD --check`와 변경 전체를 설계 요구사항별로 대조한다. 한국어 PR을 생성하고 CI의 lint, typecheck, test, migrations, security, secret-scan, e2e, Quality gate 및 CodeQL이 모두 통과할 때까지 수정한다.

- [ ] **Step 3: squash merge와 v1.3.0 릴리스**

PR을 `main`에 squash merge하고 annotated tag `v1.3.0`을 push한다. Release workflow 성공과 정식 GitHub Release 생성을 확인한다.

- [ ] **Step 4: 실제 데이터와 다른 컨테이너 기준선 기록**

PointBook 실제 SQLite 무결성과 78/44/30/4/28/1,392 집계를 확인한다. SoolJang·FamilyCard 컨테이너 ID와 시작 시각을 기록한다.

- [ ] **Step 5: 실제 서버를 Compose로 전환**

`scripts/deploy.sh`를 실행해 현재 Codex 소유 Uvicorn을 정상 종료하고 사전 백업 후 PointBook Compose 앱을 시작한다. 기존 unified exec 세션이 종료됐음을 확인한다.

- [ ] **Step 6: 실제 배포 검증**

`docker compose ps`, `/login` HTTP 200, 인증 후 `/teams` 팀 4개·색상 편집기 4개, 앱/패키지 버전 `1.3.0`, 실제 DB 집계 보존을 확인한다. SoolJang·FamilyCard 컨테이너 ID와 시작 시각이 기준선과 같은지도 확인한다.

- [ ] **Step 7: 최종 상태 보고**

PR·릴리스 링크, 컨테이너 health, 실제 DB 집계, 백업 파일명, 서버 주소, 다른 프로젝트 무변경을 한국어로 보고한다.
