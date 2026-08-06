#!/usr/bin/env bash
# WSL 상시 구동 스크립트 (백그라운드 실행)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
LOG="data/server.log"
mkdir -p data

if [ -f data/server.pid ] && kill -0 "$(cat data/server.pid)" 2>/dev/null; then
  echo "이미 실행 중입니다 (PID $(cat data/server.pid), 포트 ${PORT})"
  exit 0
fi

nohup uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG" 2>&1 &
echo $! > data/server.pid
sleep 2
echo "서버 시작: http://localhost:${PORT}"
echo "PID: $(cat data/server.pid) / 로그: ${LOG} (실행 중지: scripts/stop.sh)"
