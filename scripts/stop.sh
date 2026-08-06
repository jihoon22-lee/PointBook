#!/usr/bin/env bash
# 실행 중인 서버 중지
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f data/server.pid ]; then
  echo "실행 중인 서버가 없습니다."
  exit 0
fi

PID="$(cat data/server.pid)"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "서버 중지 (PID $PID)"
else
  echo "이미 종료된 프로세스입니다 (PID $PID)"
fi
rm -f data/server.pid
