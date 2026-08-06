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
elif [ -f data/server.pid ]; then
  echo "이전 프로세스가 종료된 상태입니다. PID 파일을 정리하고 새로 시작합니다."
  rm -f data/server.pid
fi

if command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "오류: 포트 ${PORT} 이(가) 이미 사용 중입니다." >&2
  echo "다른 포트로 실행하려면: PORT=8001 scripts/run.sh" >&2
  exit 1
fi

nohup uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG" 2>&1 &
echo $! > data/server.pid
sleep 2

if ! kill -0 "$(cat data/server.pid)" 2>/dev/null; then
  echo "오류: 서버가 즉시 종료되었습니다. 로그를 확인하세요: ${LOG}" >&2
  rm -f data/server.pid
  exit 1
fi

echo "서버 시작: http://localhost:${PORT}"
echo "PID: $(cat data/server.pid) / 로그: ${LOG} (실행 중지: scripts/stop.sh)"
