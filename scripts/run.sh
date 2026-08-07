#!/usr/bin/env bash
# WSL 상시 구동 스크립트 (백그라운드 실행)
# - PORT: 포트 (기본 8000)
# - HOST: 바인딩 주소 (기본 0.0.0.0) — tailscale serve/funnel을 사용하면 127.0.0.1 권장
# - PYTHON_BIN: 파이썬 실행기 (기본 .venv/bin/python — uv run 대비 백그라운드 안정성)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
LOG="data/server.log"
PY="${PYTHON_BIN:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="uv run python"
fi
mkdir -p data

if [ -f data/server.pid ] && kill -0 "$(cat data/server.pid)" 2>/dev/null; then
  echo "이미 실행 중입니다 (PID $(cat data/server.pid), 포트 ${PORT})"
  exit 0
elif [ -f data/server.pid ]; then
  echo "이전 프로세스가 종료된 상태입니다. PID 파일을 정리하고 새로 시작합니다."
  rm -f data/server.pid
fi

if command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -q "$HOST:$PORT "; then
  echo "오류: ${HOST}:${PORT} 이(가) 이미 사용 중입니다." >&2
  echo "원인 확인: ss -tlnp | grep :${PORT}" >&2
  echo "  - tailscale serve/funnel이 같은 포트를 점유 중일 수 있습니다: tailscale serve status" >&2
  echo "  - 다른 포트로 실행: PORT=8001 scripts/run.sh" >&2
  exit 1
fi

nohup "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 < /dev/null &
echo $! > data/server.pid

# /mnt/e 마운트 특성상 파이썬 임포트가 느려 최대 30초까지 대기
for _ in $(seq 1 15); do
  sleep 2
  if ! kill -0 "$(cat data/server.pid)" 2>/dev/null; then
    echo "오류: 서버가 종료되었습니다. 로그를 확인하세요: ${LOG}" >&2
    rm -f data/server.pid
    exit 1
  fi
  if ss -tln 2>/dev/null | grep -q "$HOST:$PORT "; then
    break
  fi
done

if ! ss -tln 2>/dev/null | grep -q "$HOST:$PORT "; then
  echo "오류: 서버가 ${PORT} 포트에 바인딩하지 못했습니다. 로그를 확인하세요: ${LOG}" >&2
  kill "$(cat data/server.pid)" 2>/dev/null || true
  rm -f data/server.pid
  exit 1
fi

echo "서버 시작: http://localhost:${PORT} (host ${HOST})"
echo "PID: $(cat data/server.pid) / 로그: ${LOG} (실행 중지: scripts/stop.sh)"
