#!/usr/bin/env bash
# 운영 Compose 구성을 실제 DB·포트와 격리해 검증한다.
set -euo pipefail
cd "$(dirname "$0")/.."

SMOKE_ROOT="$(mktemp -d /tmp/pointbook-compose-smoke.XXXXXX)"
export COMPOSE_PROJECT_NAME="pointbook-smoke-$$"
export POINTBOOK_DATA_DIR="$SMOKE_ROOT/data"
export POINTBOOK_ENV_FILE="$SMOKE_ROOT/not-present.env"
export POINTBOOK_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
export POINTBOOK_VERSION="smoke"
export POINTBOOK_UID="$(id -u)"
export POINTBOOK_GID="$(id -g)"
export ADMIN_USERNAME="smoke-admin"
export ADMIN_PASSWORD="smoke-password"
export SECRET_KEY="smoke-secret-key"
export AI_PROVIDER="mock"

mkdir -p "$POINTBOOK_DATA_DIR"

cleanup() {
  docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$SMOKE_ROOT"
}
trap cleanup EXIT

wait_for_health() {
  local container_id status
  container_id="$(docker compose ps -q app)"
  if [ -z "$container_id" ]; then
    echo "오류: 스모크 앱 컨테이너를 찾을 수 없습니다." >&2
    return 1
  fi
  for _ in $(seq 1 30); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
    case "$status" in
      healthy) return 0 ;;
      unhealthy)
        docker compose logs --tail 100 app >&2
        return 1
        ;;
    esac
    sleep 2
  done
  docker compose logs --tail 100 app >&2
  return 1
}

docker compose up -d --build app
wait_for_health
curl --fail --silent --show-error --max-time 5 \
  "http://127.0.0.1:${POINTBOOK_PORT}/login" >/dev/null

docker compose exec -T app python -c \
  "import sqlite3; c=sqlite3.connect('/app/data/pointbook.db'); c.execute('CREATE TABLE pointbook_smoke_marker (value TEXT NOT NULL)'); c.execute(\"INSERT INTO pointbook_smoke_marker VALUES ('persisted')\"); c.commit()"

docker compose restart app
wait_for_health

MARKER="$(docker compose exec -T app python -c \
  "import sqlite3; c=sqlite3.connect('/app/data/pointbook.db'); print(c.execute('SELECT value FROM pointbook_smoke_marker').fetchone()[0])")"
if [ "$MARKER" != "persisted" ]; then
  echo "오류: Compose 재시작 후 SQLite 표식 데이터가 유지되지 않았습니다." >&2
  exit 1
fi

echo "운영 Compose 스모크 통과: healthy, HTTP 200, SQLite 영속성 확인"
