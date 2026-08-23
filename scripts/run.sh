#!/usr/bin/env bash
# Docker Compose 운영 서버 시작
set -euo pipefail
cd "$(dirname "$0")/.."

export POINTBOOK_PORT="${POINTBOOK_PORT:-${PORT:-8002}}"

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
