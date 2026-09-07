# 백업·복원·배포

R08/R09/R25/R26 · WP03 #51 · S06/S07. 운영 자료는 호스트에 보관하며 공개 PR/CI에는
합성 자료만 사용한다. 운영 명령은 기존 승인 범위와 실제 서비스 설정을 확인한 뒤 실행한다.

## 경로와 설정

`run.sh`, `stop.sh`, `deploy.sh`, `scheduled-backup.sh`, `restore.sh`는
`scripts/compose-common.sh`에서 선택한 `POINTBOOK_ENV_FILE`을 Compose `--env-file`과
서비스 `env_file`에 함께 사용한다. `environment`에서 비밀키·관리자·AI 기본값을 다시
넣지 않는다. 셸의 `POINTBOOK_*` override가 우선이며, 선택 env 파일의 경로 변수도
Compose의 실제 mount 해석 결과에 반영한다. 설정 전체나 비밀값을 로그에 쓰지 않는다.

- 호스트 `POINTBOOK_DATA_DIR` → 컨테이너 `/app/data`, DB `/app/data/pointbook.db`.
- 호스트 `POINTBOOK_BACKUP_DIR` → `/app/backups`, 기본은 선택 데이터 디렉터리의 `backups`.
- Compose 안에서 `DATABASE_PATH`와 `BACKUP_DIR`는 위 mount 계약으로 고정된다.
- `COMPOSE_PROJECT_NAME`, 데이터·환경·백업 경로를 모든 운영 명령에서 동일하게 사용한다.
- 실제 컨테이너 UID/GID로 쓰기·공간을 사전 검사한다. 보호 경로는 해당 UID만 읽고 쓸 수
  있게 운영자가 만들고, 신규 백업 파일은 0600으로 생성한다. 같은 디스크의 다른 폴더는
  호스트/디스크 장애의 독립 복구책이 아니다. 승인된 별도 디스크 mount를 지정할 수 있다.

```bash
export COMPOSE_PROJECT_NAME=pointbook
export POINTBOOK_ENV_FILE=/보호경로/pointbook.env
export POINTBOOK_DATA_DIR=/보호경로/pointbook-data
export POINTBOOK_BACKUP_DIR=/별도디스크/pointbook-backups
scripts/scheduled-backup.sh
```

호스트 Python CLI도 `POINTBOOK_ENV_FILE`을 읽는다. 명시한 `DATABASE_PATH`·`BACKUP_DIR`이
우선하며 없을 때 `POINTBOOK_DATA_DIR/pointbook.db`·`POINTBOOK_BACKUP_DIR`을 따른다.
선택 환경 파일이 없으면 기본 파일로 우회하지 않고 실패한다. Compose는 컨테이너의 경로를
명시하고 호스트 환경 파일 경로를 재해석하지 않는다. 운영 백업에는 wrapper를 권장한다.

`stop.sh`는 선택된 Compose의 `app`만 중지한다. 과거 `server.pid`나 호스트 Uvicorn을
자동 종료하지 않는다. legacy 프로세스가 있다면 소유권을 확인한 운영 전환 절차로 중지한다.

## 일관된 백업과 정기 실행

SQLite backup API로 WAL을 포함한 일관된 사본을 취득한다. 임시 DB의 연결을 닫고
무결성·외래키·최소 구조·총잔액 공식을 검사한다. 그 사본 자체의 revision, 구조 해시,
테이블별 행 수, 장부 합계와 DB SHA-256을 metadata에 기록한다. 같은 시점이 아닌
원본 합계를 다시 읽어 대사하지 않는다. usage는 음수도 그대로 보존한다.

0600 고유 임시 파일을 검증·fsync한 뒤 metadata, 최종 `.db` 순서로 원자 교체한다.
실패 임시 파일은 성공 목록에 포함하지 않는다. `BACKUP_KEEP`는 1~10000이며 새 사본이
검증된 다음 원본 DB 옆 `.pointbook.db.backup-owner` 파일의 영속 UUID가 같은 검증 사본만 정리한다.
호스트/컨테이너 경로 표기가 달라도 동일 mount의 UUID를 사용하고, 서로 다른 데이터
디렉터리는 별도 UUID로 분리한다. DB와 이 소유권 파일을 함께 보존한다. DB와 같은 이름의
다른 위치 DB, metadata 없는 수동 사본, 손상 사본은 자동 삭제하지 않는다.

월간 확정과 CLI는 동일 `backup_database()`를 사용한다. 확정 호출자는 쓰기 트랜잭션
확보·승인 버전 재검증 **후, 첫 DML/flush 전** 호출한다. 별도 읽기 연결이라 미커밋
데이터를 포함하지 않는다. 첫 변경 뒤 같은 트랜잭션에서 호출하면 write lock 대기/timeout이
날 수 있으므로 호출 순서를 지킨다. 이관 dry-run도 SQLite backup API로 사본을 만든다.

호스트 스케줄은 `scripts/scheduled-backup.sh` 하나로 통일한다. `.maintenance.lock`의
비차단 flock으로 중복 실행/배포·복원과의 경합을 거부한다. 앱 백업끼리의 생성·보관 정리는
별도 DB별 flock으로 직렬화된다. 스크립트 성공/실패는 종료 코드로, 최근 검증 결과는
데이터 디렉터리의 `recovery-status.json`에 남는다. 운영 화면 API는 시간·상태만 제공한다.

정기 백업·복원은 현재 앱 컨테이너의 immutable 이미지 ID를 고정한다. 컨테이너가
없으면 명시한 `POINTBOOK_DEPLOY_IMAGE` 또는 프로젝트의 설정 이미지 ID를 확인해
고정한다. 운영 중 공유 태그가 변경돼도 다른 앱 코드를 실행하지 않는다. 기동/배포 preflight는
선택 이미지의 운영 설정과 기존/신규 관리자 기동 조건을 검사하고, 현재 DB의 SQLite 사본을 임시 Engine에서 실제
이전·구조·합계 검증한다. 실패하면 기존 서비스를 중지하지 않는다.

호스트 crontab 예시(매일 02:15, 실제 설치는 승인된 운영 변경으로 수행):

```cron
15 2 * * * POINTBOOK_ENV_FILE=/보호경로/pointbook.env POINTBOOK_DATA_DIR=/보호경로/pointbook-data POINTBOOK_BACKUP_DIR=/별도디스크/pointbook-backups COMPOSE_PROJECT_NAME=pointbook /home/jihoon/projects/PointBook/scripts/scheduled-backup.sh >>/보호경로/pointbook-backup.log 2>&1
```

로그 파일도 소유자 전용 권한으로 사전 생성하고 호스트의 기존 로그 보관 정책을 적용한다.
외부 클라우드 계정·동기화·새 시스템 서비스는 이 기능에서 설치하지 않는다.

## 복원 rehearsal과 실제 복원

검증 사본은 `.db`와 같은 이름의 `.json`이 한 쌍이다. 기존 파일 복사만 있는 백업은
자동으로 신뢰하지 않는다. 격리 사본에서 구조·revision·합계를 확인해 검증 백업으로
전환한 후 사용한다. metadata에는 집계가 포함되므로 둘 다 공개 저장소에 올리지 않는다.

```bash
scripts/restore.sh /별도디스크/pointbook-backups/선택사본.db
```

1. 현재 이미지 ID와 선택 환경을 고정하고 읽기 전용 mount의 사본 hash·metadata·DB를
   검증한다. 임시 DB/독립 Engine에서 무버전 스키마 판별, 실제 마이그레이션, 현재 스키마·
   장부 행 수·합계 대사와 운영 관리자 암호/초기 생성 조건까지 확인한다. 알 수 없는 무버전/미지원 revision/이전 실패는
   서비스를 멈추거나 현재 DB를 교체하기 전에 차단한다. 전역 운영 Engine은 바꾸지 않는다.
2. 대상 `app` 쓰기를 중지한다. 같은 DB를 쓰는 별도 호스트 프로세스가 없어야 한다.
3. 같은 filesystem 임시 경로에서 사본을 재검증한다. 기존 DB를 `restore-preserved/`에
   검증 보존하고, WAL checkpoint 완료를 확인한다. 남은 sidecar는 별도 경로에 보존한다.
4. DB를 원자 교체하고 revision·행 수·합계를 대사한다. 앱 기동 시 필요한 마이그레이션을
   수행하며 `/health`의 DB readiness, `/login`, DB 읽기·무결성 검사를 확인한다.
5. 앱 재시작 후 같은 검사로 bind mount 영속성을 확인한다. 실제 업무 수용은 인증 후
   조회·필요한 합계 대사를 추가한다. 자동 검사 통과가 실기 업무 수용을 대체하지 않는다.

기존 DB가 손상돼 안전한 보존 백업조차 만들 수 없으면 자동 교체를 거부한다. 원본 DB와
sidecar를 모두 보존한 별도 수동 구조 복구가 필요하다. `restore-preserved/`는 같은 소유 DB의 검증 사본을 최대 10,000개 보존한다.
한도를 넘으면 새 사본 검증 후 오래된 사본부터 정리하며 운영자는 복원 수용 후 필요에
따라 먼저 정리할 수 있다. 기본 `BACKUP_KEEP`와는 별도의 복구 보존 정책이다.

## 배포·실패 경계

`deploy.sh`는 깨끗한 checkout에서 기본 `origin/main`, 또는 명시한
`POINTBOOK_DEPLOY_REF`를 detached checkout하고 새 SHA의 배포 스크립트로 exec
handoff한 뒤 유지보수 lock을 한 번 확보한다. 예전 source에서 읽은 함수를 이어 실행하지 않는다. 기본 main을 직접 수정하거나 푸시하지
않는다. 배포 완료 SHA를 데이터 경로에 기록한다. 실제 배포 전 해당 SHA의 CI를 확인한다.

기본 이미지 태그는 Compose 프로젝트명과 `POINTBOOK_VERSION`으로 분리한다.
`POINTBOOK_DEPLOY_IMAGE`를 명시하면 이미 검증한 로컬 이미지 ID를 사용하며 재빌드하지
않는다. 생략하면 현재 source로 빌드하고 즉시 ID를 고정한다. 이후 preflight·백업·기동은
모두 그 ID를 사용하며 실제 컨테이너 `.Image`와 비교한다. 완료 source와 이미지 ID를
각각 `deployed-source.txt`, `deployed-image.txt`에 기록한다. 명시한 이미지가 해당
검증 source에서 만들어졌는지는 배포자가 CI/이미지 증거로 확인한다.

```bash
POINTBOOK_DEPLOY_REF=검증한SHA POINTBOOK_DEPLOY_IMAGE=sha256:검증한이미지ID scripts/deploy.sh
```

`run.sh`도 기존 DB가 있으면 같은 이미지 재기동이라도 preflight 후 대상 앱 정지 →
검증 백업 → 기동·대사를 수행한다. 새 설치에는 백업할 기존 DB가 없다.

실행 순서는 이전 컨테이너 이미지 ID·rollback 후보 태그 보존 → 새 이미지 빌드 →
컨테이너 권한·공간·운영 설정 검증 → 대상 앱 중지 → 검증 DB 백업 → 앱 기동/마이그레이션 →
DB readiness·읽기·무결성 → 재시작·재검증 → 사전 백업 기준 장부 행 수·합계 대사다. 이전 이미지 정보는 `previous-image.txt`에
남는다. 이전 이미지가 새 스키마를 읽을 수 있다고 가정하지 않는다.

| 실패 시점 | 유지되는 상태와 다음 판단 |
|---|---|
| 빌드/사전 권한·공간 검사 | 기존 앱 계속 실행. 원인을 고친 뒤 같은 SHA로 재시도 |
| 정지 후 백업 | 앱 정지, 원본 DB 유지. 권한/공간/무결성 확인 후 백업 재시도 |
| 기동/마이그레이션/health | 검증 사전 사본과 이전 이미지 보존. 실패 DB도 보존하고 호환성을 확인해 명시적 복원 선택 |
| 복원 사본 검증 | 앱 중지 전이면 기존 앱 계속 실행. 다른 검증 사본 선택 |
| 기존 DB 보존/교체 | 기존 검증 보존 사본 확인. 자동 재시도보다 실제 파일·서비스 상태 먼저 확인 |
| 쓰기 재개 후 | 새 입력이 있을 수 있음. **자동 전체 DB rollback 금지**. 현재 DB 추가 보존·입력 대사 후 명시적 복구 계획 |

앱이 새로 쓰기를 허용한 순간부터 health 실패 여부와 무관하게 자동 DB rollback하지 않는다.
이전 이미지 후보 태그는 수동 선택 자료이며 스키마 downgrade 보장이 아니다.
대사 전에 다른 사용자가 새 입력을 저장했다면 합계 차이를 자동 오류 수정하지 않고
신규 입력과 이전 대상을 구분해 운영자가 확인한다. 배포 수용 중에는 업무 입력을 중단한다.

합성 검증: `pytest tests/test_backup.py tests/test_restore.py tests/test_deploy_script.py
 tests/test_docker_deployment.py`, `bash e2e/production-smoke.sh`.
후자는 임시 경로·별도 프로젝트/포트에서 custom 환경값, 별도 백업, 복원·재시작을 실행한다.
