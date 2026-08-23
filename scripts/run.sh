#!/usr/bin/env bash
# Docker Compose 운영 서버 시작
set -euo pipefail
cd "$(dirname "$0")/.."

export POINTBOOK_PORT="${POINTBOOK_PORT:-${PORT:-8002}}"
export POINTBOOK_DATA_DIR="${POINTBOOK_DATA_DIR:-./data}"
export POINTBOOK_UID="${POINTBOOK_UID:-$(id -u)}"
export POINTBOOK_GID="${POINTBOOK_GID:-$(id -g)}"

mkdir -p "$POINTBOOK_DATA_DIR"
if [ ! -w "$POINTBOOK_DATA_DIR" ]; then
  echo "오류: PointBook 데이터 디렉터리에 쓸 수 없습니다: $POINTBOOK_DATA_DIR" >&2
  exit 1
fi

docker compose up -d --build app

CONTAINER_ID="$(docker compose ps -q app)"
if [ -z "$CONTAINER_ID" ]; then
  echo "오류: PointBook 앱 컨테이너를 찾을 수 없습니다." >&2
  docker compose ps >&2
  exit 1
fi

for _ in $(seq 1 30); do
  STATUS="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || true)"
  case "$STATUS" in
    healthy)
      echo "서버 시작: http://localhost:${POINTBOOK_PORT} (Docker Compose, healthy)"
      exit 0
      ;;
    "unhealthy")
      echo "오류: PointBook 앱 컨테이너가 unhealthy 상태입니다." >&2
      docker compose logs --tail 100 app >&2
      exit 1
      ;;
  esac
  if ! docker inspect --format '{{.State.Running}}' "$CONTAINER_ID" 2>/dev/null | grep -q true; then
    echo "오류: PointBook 앱 컨테이너가 종료되었습니다." >&2
    docker compose logs --tail 100 app >&2
    exit 1
  fi
  sleep 2
done

echo "오류: PointBook 앱 컨테이너의 healthy 전환 시간이 초과됐습니다." >&2
docker compose logs --tail 100 app >&2
exit 1
