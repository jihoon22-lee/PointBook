# Docker Compose + SQLite 운영 전환 설계

## 상태

- 승인일: 2026-08-23
- 대상 저장소: `/mnt/e/projects/PointBook`
- 목표 버전: `1.3.0`

## 배경

PointBook은 단일 관리자가 내부망에서 사용하는 소규모 서비스다. 현재 실제 데이터는
SQLite `data/pointbook.db`에 있으며, 계정 78개와 월별 기록 1,392건을 보유한다. 데이터
규모와 동시성 요구를 고려하면 PostgreSQL 전환의 이점보다 운영 복잡도가 크므로 SQLite를
유지한다.

현재 Uvicorn은 Codex 실행 세션의 자식 프로세스로 실행돼 세션 정리 시 함께 종료될 수 있다.
이미 WSL에서 Docker가 SoolJang을 상시 운영하고 있으므로 PointBook도 Docker Compose의
재시작 정책으로 운영해 대화 세션과 서버 수명을 분리한다.

## 목표

- PointBook을 Docker Compose의 단일 앱 컨테이너로 상시 실행한다.
- 현재 SQLite 파일과 백업을 변환 없이 그대로 보존한다.
- 컨테이너 장애와 Docker 재시작 후 앱이 자동으로 다시 기동되게 한다.
- 실제 서비스는 계속 `127.0.0.1:8002`로 제공한다.
- 배포 시 코드 최신화, 이미지 빌드, 서버 중지, DB 백업, 새 컨테이너 시작, 상태 확인을
  일관된 순서로 수행한다.
- 기존 단위 테스트와 구버전 Chromium E2E에 운영 Compose 검증을 추가한다.

## 비목표

- PostgreSQL 또는 다른 DB 엔진으로 전환하지 않는다.
- SQLite 데이터를 Docker named volume로 옮기지 않는다.
- GHCR 이미지 게시나 원격 오케스트레이션을 도입하지 않는다.
- Tailscale, 방화벽, TLS 구성을 변경하지 않는다.
- 실제 팀 색상, 인원, 잔액, 월별 이력을 변경하지 않는다.

## 운영 구조

저장소 루트에 운영용 `Dockerfile`과 `docker-compose.yml`을 둔다. Compose는 `app` 서비스
하나만 관리한다.

### 앱 컨테이너

- `uv.lock`을 사용해 운영 의존성을 재현 가능하게 설치한다.
- Uvicorn을 `0.0.0.0:8000`으로 실행하고 호스트에서는 `127.0.0.1:8002`로만 공개한다.
- `init: true`로 PID 1의 신호 전달과 자식 프로세스 회수를 보장한다.
- `restart: unless-stopped`로 Codex 세션과 독립적으로 재시작한다.
- 상태 확인은 컨테이너 내부에서 `/login`의 HTTP 200 응답을 검사한다.
- 종료 유예 시간을 두어 Uvicorn이 정상적으로 연결을 닫게 한다.
- 애플리케이션 소스와 의존성은 이미지에 포함하되 `.env`, `data/`, 테스트 생성물은
  `.dockerignore`로 제외한다.

### 설정과 비밀정보

- Compose는 저장소의 `.env`를 런타임 환경 변수로 읽는다.
- `.env`의 내용은 이미지나 Git 이력에 포함하지 않는다.
- 컨테이너 안에서는 `DATABASE_PATH=/app/data/pointbook.db`를 사용한다.
- 기존 관리자 계정과 세션 비밀키를 그대로 사용한다.

## 데이터 보존

호스트의 `${POINTBOOK_DATA_DIR:-./data}`를 컨테이너 `/app/data`에 bind mount한다. 기본
운영 경로는 기존 `data/`이므로 다음 파일이 이미지 교체와 컨테이너 재생성 뒤에도 그대로
남는다.

- `data/pointbook.db`
- `data/backups/*.db`
- 운영 로그나 향후 생성되는 데이터 파일

SQLite 스키마나 내용을 변환하지 않는다. 최초 전환 전과 전환 후에 다음 집계가 같아야 한다.

- 전체 계정 78개
- 일반 재직 44명
- 일반 비재직 30명
- 공용 계정 4개
- 월 28개
- 잔액 기록 1,392건
- 최신 월 `2026-08`

## 실행 스크립트

### `scripts/run.sh`

- Docker Compose 운영 서버를 빌드하고 백그라운드로 시작한다.
- 컨테이너가 healthy가 될 때까지 제한 시간 안에서 기다린다.
- 실패하면 Compose 상태와 로그 확인 명령을 안내하고 0이 아닌 코드로 종료한다.

### `scripts/stop.sh`

- 운영 Compose 컨테이너를 정상 중지한다.
- 최초 전환을 위해 기존 `data/server.pid` 방식 Uvicorn이 있으면 해당 프로세스도 정확히
  확인해 종료하고 PID 파일을 정리한다.
- 다른 프로젝트의 컨테이너나 프로세스는 건드리지 않는다.

### `scripts/deploy.sh`

배포 순서는 다음으로 고정한다.

1. `main`을 fast-forward 방식으로 최신화한다.
2. 새 이미지를 빌드해 빌드 실패를 서비스 중지 전에 확인한다.
3. PointBook Compose 또는 기존 PID 방식 서버만 정상 중지한다.
4. `data/pointbook.db`를 시각이 포함된 `data/backups/pointbook-pre-deploy-*.db`로 복사한다.
5. 새 Compose 컨테이너를 시작한다.
6. healthy 상태와 `http://127.0.0.1:8002/login` HTTP 200을 확인한다.

빌드, 백업, 기동 또는 상태 확인이 실패하면 성공 메시지를 출력하지 않고 즉시 실패한다.
백업 파일과 기존 SQLite 원본은 보존하며 자동 삭제나 자동 복원을 하지 않는다.

## 최초 전환 절차

1. 실제 DB 집계와 SQLite 무결성 검사를 기록한다.
2. 운영 이미지를 빌드한다.
3. 현재 Codex 실행 세션 소유 Uvicorn만 정상 종료한다.
4. 사전 백업을 생성한다.
5. Compose 서비스를 `up -d`로 시작한다.
6. Compose 상태, healthcheck, 로그인 페이지, 인증 후 팀 화면을 확인한다.
7. 앱·패키지 버전과 실제 DB 집계를 다시 확인한다.
8. 기존 Codex 실행 세션이 종료된 상태에서도 8002 응답이 유지되는지 확인한다.

중단 시간은 기존 프로세스 종료부터 새 컨테이너 healthy 확인까지로 제한한다.

## 오류 처리와 복구

- 8002 포트가 PointBook 외 프로세스에 의해 사용 중이면 대상을 종료하지 않고 배포를
  중단한다.
- 컨테이너 시작이 실패하면 `docker compose ps`와 최근 앱 로그를 남긴다.
- DB 무결성 또는 집계가 달라지면 컨테이너를 중지하고 원본 및 사전 백업을 보존한 채
  배포 실패로 보고한다.
- 수동 복구는 검증된 사전 백업을 `data/pointbook.db`로 복원한 뒤 이전 Git 태그에서
  이미지를 다시 빌드하는 방식으로 수행한다. 자동 덮어쓰기는 하지 않는다.

## 테스트와 검증

### 자동 테스트

- Compose 렌더링 검증: `docker compose config`
- 운영 이미지 빌드 검증
- 임시 데이터 디렉터리와 전용 포트를 사용한 운영 Compose 스모크 테스트
- `/login` 상태 확인과 컨테이너 healthy 확인
- 컨테이너 재시작 후 같은 임시 SQLite 데이터가 유지되는지 확인
- 배포 스크립트의 빌드 → 중지 → 백업 → 시작 → 상태 확인 순서 회귀 테스트
- 기존 pytest, Ruff, mypy, Alembic, pip-audit, 구버전 Chromium E2E 전체 실행

실제 DB나 `.env`는 테스트 이미지, CI 산출물 또는 Git에 포함하지 않는다.

### 실제 배포 검증

- `docker compose ps`에서 PointBook 앱이 healthy
- `http://127.0.0.1:8002/login` HTTP 200
- 인증 후 `/teams`에서 기존 팀 4개와 색상 편집기 4개 확인
- 실제 DB 집계가 전환 전 기준값과 일치
- PointBook 이외 SoolJang·FamilyCard 컨테이너의 ID와 시작 시각이 바뀌지 않음

## 문서와 릴리스

- README와 사용 가이드의 상시 실행·중지·로그·배포 명령을 Docker Compose 기준으로 바꾼다.
- 아키텍처 문서의 서버 운영 구조를 Docker + SQLite bind mount로 갱신한다.
- 변경 이력에 데이터 변환이 없고 기존 SQLite를 그대로 사용한다는 점을 명시한다.
- 앱 버전을 `1.3.0`으로 올린다.
- 기능 브랜치에서 PR을 만들고 Quality gate 통과 후 squash merge한다.
- `v1.3.0` 릴리스 생성 후 실제 서버를 Compose로 전환한다.
