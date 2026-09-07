#!/usr/bin/env bash
# 실제 운영 파일 없는 custom Compose 환경에서 백업·복원·재시작을 검증한다.
set -euo pipefail
cd "$(dirname "$0")/.."
SMOKE_ROOT="$(mktemp -d /tmp/pointbook-compose-smoke.XXXXXX)"
export COMPOSE_PROJECT_NAME="pointbook-smoke-$$"
export POINTBOOK_DATA_DIR="$SMOKE_ROOT/data"
export POINTBOOK_BACKUP_DIR="$SMOKE_ROOT/protected-backups"
export POINTBOOK_ENV_FILE="$SMOKE_ROOT/custom.env"
export POINTBOOK_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
export POINTBOOK_VERSION="smoke-$$"
export POINTBOOK_UID="$(id -u)"
export POINTBOOK_GID="$(id -g)"
umask 077
cat > "$POINTBOOK_ENV_FILE" <<'ENV'
APP_ENV=test
ADMIN_USERNAME=smoke-admin
ADMIN_PASSWORD=smoke-password-synthetic
SECRET_KEY=smoke-secret-key-synthetic-32-characters # gitleaks:allow -- 합성 테스트 전용
AI_PROVIDER=mock
ENV
source scripts/compose-common.sh
cleanup() {
  pb_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$SMOKE_ROOT"
}
trap cleanup EXIT
scripts/run.sh
pb_compose exec -T app python -c \
  'from app.config import get_settings; s=get_settings(); assert s.admin_username=="smoke-admin"; assert s.backup_dir=="/app/backups"; assert s.database_path=="/app/data/pointbook.db"'
pb_compose exec -T app python -c \
  "import sqlite3; c=sqlite3.connect('/app/data/pointbook.db'); c.execute('CREATE TABLE pointbook_smoke_marker (value TEXT NOT NULL)'); c.execute(\"INSERT INTO pointbook_smoke_marker VALUES ('persisted')\"); c.commit(); c.close()"
scripts/scheduled-backup.sh
BACKUP_FILE="$(find "$POINTBOOK_BACKUP_DIR" -maxdepth 1 -name '*.db' -print -quit)"
[ -n "$BACKUP_FILE" ] || { echo '오류: 별도 보호 경로에 검증 사본이 없습니다.' >&2; exit 1; }
pb_compose exec -T app python -c \
  "import sqlite3; c=sqlite3.connect('/app/data/pointbook.db'); c.execute(\"UPDATE pointbook_smoke_marker SET value='after-backup'\"); c.commit(); c.close()"
scripts/restore.sh "$BACKUP_FILE"
pb_compose restart app
pb_verify
MARKER="$(pb_compose exec -T app python -c \
  "import sqlite3; c=sqlite3.connect('/app/data/pointbook.db'); print(c.execute('SELECT value FROM pointbook_smoke_marker').fetchone()[0]); c.close()")"
[ "$MARKER" = persisted ] || { echo '오류: 복원·재시작 후 합성 표식 대사가 실패했습니다.' >&2; exit 1; }
echo '운영 Compose 스모크 통과: custom env/mount, DB readiness, 보호 경로 백업, 복원, 재시작 영속성'
