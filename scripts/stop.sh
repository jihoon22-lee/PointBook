#!/usr/bin/env bash
# Docker Compose 운영 서버와 이 저장소가 소유한 legacy Uvicorn만 중지
set -euo pipefail
cd "$(dirname "$0")/.."

STOPPED=false
PROJECT_ROOT="$(pwd -P)"
COMPOSE_ID="$(docker compose ps -a -q app 2>/dev/null || true)"

if [ -n "$COMPOSE_ID" ]; then
  docker compose stop app
  echo "Docker Compose 서버 중지"
  STOPPED=true
fi

if [ -f data/server.pid ]; then
  PID="$(tr -d '\r\n' < data/server.pid)"
  case "$PID" in
    ""|*[!0-9]*)
      echo "오류: 올바르지 않은 legacy PID 파일입니다: data/server.pid" >&2
      exit 1
      ;;
  esac

  if kill -0 "$PID" 2>/dev/null; then
    COMMAND_LINE="$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true)"
    PROCESS_CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
    if [[ "$COMMAND_LINE" != *"uvicorn app.main:app"* || "$PROCESS_CWD" != "$PROJECT_ROOT" ]]; then
      echo "오류: PID $PID 은(는) 이 PointBook 저장소가 소유한 Uvicorn이 아닙니다." >&2
      exit 1
    fi

    kill "$PID"
    for _ in $(seq 1 20); do
      if ! kill -0 "$PID" 2>/dev/null; then
        rm -f data/server.pid
        echo "legacy 서버 중지 (PID $PID)"
        STOPPED=true
        break
      fi
      sleep 0.5
    done
    if kill -0 "$PID" 2>/dev/null; then
      echo "오류: 서버 프로세스 종료를 확인하지 못했습니다 (PID $PID)" >&2
      exit 1
    fi
  else
    rm -f data/server.pid
    echo "종료된 legacy PID 파일 정리 (PID $PID)"
  fi
fi

if [ "$STOPPED" = false ]; then
  echo "실행 중인 PointBook 서버가 없습니다."
fi
