#!/usr/bin/env bash
# GitHub Actions self-hosted runner가 호출하는 배포 스크립트 (WSL 실서버용)
# - 코드 최신화(main) → 의존성 설치 → DB 사전 백업 → 서버 재시작
# - 스키마 마이그레이션은 서버 기동 시 app.init_db → run_migrations()가 자동 적용
set -euo pipefail
cd "$(dirname "$0")/.."

# snap 기반 uv를 PATH에서 찾을 수 있도록 보장 (systemd 서비스 환경 대응)
export PATH="$PATH:/snap/bin"

echo "== deploy: 코드 최신화 =="
git fetch origin main --quiet
git checkout main --quiet
git pull --ff-only origin main --quiet

echo "== deploy: 의존성 설치 =="
uv sync

echo "== deploy: 서버 중지 =="
scripts/stop.sh

echo "== deploy: DB 사전 백업 =="
mkdir -p data/backups
if [ -f data/pointbook.db ]; then
  cp data/pointbook.db "data/backups/pointbook-pre-deploy-$(date +%Y%m%d-%H%M%S).db"
  echo "   백업 완료: data/backups/"
else
  echo "   백업할 DB 없음 (신규 설치)"
fi

echo "== deploy: 서버 재시작 =="
HOST="${DEPLOY_HOST:-127.0.0.1}" PORT="${DEPLOY_PORT:-8002}" scripts/run.sh

echo "== deploy: 완료 =="
