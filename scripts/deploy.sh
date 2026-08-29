#!/usr/bin/env bash
# GitHub Actions self-hosted runner가 호출하는 Docker Compose 배포 스크립트
# - 코드 최신화(main) → 이미지 빌드 → PointBook 중지 → DB 백업 → 기동·상태 확인
set -euo pipefail
cd "$(dirname "$0")/.."

export PATH="$PATH:/snap/bin"
export POINTBOOK_PORT="${POINTBOOK_PORT:-${DEPLOY_PORT:-8002}}"
export POINTBOOK_UID="${POINTBOOK_UID:-$(id -u)}"
export POINTBOOK_GID="${POINTBOOK_GID:-$(id -g)}"

echo "== deploy: 코드 최신화 =="
git fetch origin main --quiet
git checkout main --quiet
git pull --ff-only origin main --quiet

echo "== deploy: 운영 이미지 빌드 =="
docker compose build app

echo "== deploy: PointBook 서버 중지 =="
scripts/stop.sh

echo "== deploy: DB 사전 백업 =="
mkdir -p data/backups
if [ -f data/pointbook.db ]; then
  cp data/pointbook.db "data/backups/pointbook-pre-deploy-$(date +%Y%m%d-%H%M%S).db"
  echo "   백업 완료: data/backups/"
else
  echo "   백업할 DB 없음 (신규 설치)"
fi

echo "== deploy: Docker Compose 서버 시작 =="
docker compose up -d app

CONTAINER_ID="$(docker compose ps -q app)"
if [ -z "$CONTAINER_ID" ]; then
  echo "오류: PointBook 앱 컨테이너를 찾을 수 없습니다." >&2
  docker compose ps >&2
  exit 1
fi

for _ in $(seq 1 30); do
  STATUS="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || true)"
  case "$STATUS" in
    healthy) break ;;
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

if [ "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID")" != "healthy" ]; then
  echo "오류: PointBook 앱 컨테이너의 healthy 전환 시간이 초과됐습니다." >&2
  docker compose logs --tail 100 app >&2
  exit 1
fi

for _ in $(seq 1 10); do
  if curl --fail --silent --show-error --max-time 5 \
    "http://127.0.0.1:${POINTBOOK_PORT}/login" >/dev/null; then
    echo "== deploy: 완료 (http://localhost:${POINTBOOK_PORT}, healthy) =="
    exit 0
  fi
  sleep 1
done

echo "오류: PointBook 로그인 페이지 상태 확인에 실패했습니다." >&2
docker compose logs --tail 100 app >&2
exit 1
