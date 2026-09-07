#!/usr/bin/env bash
# 실행·중지·백업·복원·배포가 공유하는 경로/Compose 계약. 직접 실행하지 않는다.
POINTBOOK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$POINTBOOK_ROOT"
export POINTBOOK_ENV_FILE="$(realpath -m "${POINTBOOK_ENV_FILE:-$POINTBOOK_ROOT/.env}")"
POINTBOOK_INTERPOLATION_FILE="$POINTBOOK_ENV_FILE"
if [ ! -f "$POINTBOOK_INTERPOLATION_FILE" ]; then POINTBOOK_INTERPOLATION_FILE=/dev/null; fi
# --env-file 지정으로 저장소 기본 .env가 custom 설정에 끼어들지 않게 한다.
pb_compose() { docker compose --env-file "$POINTBOOK_INTERPOLATION_FILE" -f "$POINTBOOK_ROOT/docker-compose.yml" "$@"; }
export POINTBOOK_UID="${POINTBOOK_UID:-$(id -u)}"
export POINTBOOK_GID="${POINTBOOK_GID:-$(id -g)}"
if [ -n "${POINTBOOK_PORT:-${DEPLOY_PORT:-${PORT:-}}}" ]; then
  export POINTBOOK_PORT="${POINTBOOK_PORT:-${DEPLOY_PORT:-${PORT:-}}}"
fi
# Compose가 선택된 env 파일과 셸 override를 해석한 실제 mount/port만 가져온다.
# 전체 config(비밀값 포함)는 출력하거나 파일에 저장하지 않는다.
POINTBOOK_RESOLVED="$(pb_compose config --format json | python3 -c '
import json,sys
configuration=json.load(sys.stdin)
app=configuration["services"]["app"]
mounts={v["target"]:v["source"] for v in app["volumes"] if v["type"]=="bind"}
print(mounts["/app/data"])
print(mounts["/app/backups"])
print(app["ports"][0]["published"])
print(app["image"])
print(configuration["name"])
')"
mapfile -t POINTBOOK_PATHS <<< "$POINTBOOK_RESOLVED"
export POINTBOOK_DATA_DIR="$(realpath -m "${POINTBOOK_PATHS[0]}")"
export POINTBOOK_BACKUP_DIR="$(realpath -m "${POINTBOOK_PATHS[1]}")"
export POINTBOOK_PORT="${POINTBOOK_PATHS[2]}"
POINTBOOK_CONFIG_IMAGE="${POINTBOOK_PATHS[3]}"
export COMPOSE_PROJECT_NAME="${POINTBOOK_PATHS[4]}"

# Compose는 매 호출마다 mutable tag를 다시 해석하지 않고 작업 시작의 image ID를 사용한다.
pb_pin_image() {
  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$1")"
  if [[ ! "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    echo '오류: 사용할 로컬 이미지 ID를 확인할 수 없습니다.' >&2; return 1
  fi
  export POINTBOOK_IMAGE="$image_id"
  POINTBOOK_SELECTED_IMAGE="$image_id"
}

pb_pin_current_image() {
  local container_id current_image
  container_id="$(pb_compose ps -a -q app)"
  if [ -n "$container_id" ]; then
    current_image="$(docker inspect --format '{{.Image}}' "$container_id")"
    pb_pin_image "$current_image"
  else
    pb_pin_image "${POINTBOOK_DEPLOY_IMAGE:-$POINTBOOK_CONFIG_IMAGE}"
  fi
}

pb_preserve_current_image() {
  local container_id previous_image rollback_tag
  container_id="$(pb_compose ps -a -q app)"
  if [ -n "$container_id" ]; then
    previous_image="$(docker inspect --format '{{.Image}}' "$container_id")"
    rollback_tag="${COMPOSE_PROJECT_NAME}:rollback-$(date -u +%Y%m%dT%H%M%S)-$$"
    docker image tag "$previous_image" "$rollback_tag"
    (umask 077; printf '%s\n%s\n' "$previous_image" "$rollback_tag" > "$POINTBOOK_DATA_DIR/previous-image.txt")
  fi
}

pb_select_deploy_image() {
  if [ -n "${POINTBOOK_DEPLOY_IMAGE:-}" ]; then
    pb_pin_image "$POINTBOOK_DEPLOY_IMAGE"
  else
    pb_compose build app
    pb_pin_image "$POINTBOOK_CONFIG_IMAGE"
  fi
}

pb_assert_image() {
  local container_id actual
  container_id="$(pb_compose ps -q app)"
  [ -n "$container_id" ] || { echo '오류: 앱 컨테이너가 없습니다.' >&2; return 1; }
  actual="$(docker inspect --format '{{.Image}}' "$container_id")"
  if [ "$actual" != "$POINTBOOK_SELECTED_IMAGE" ]; then
    echo '오류: 실행 컨테이너가 선택한 이미지 ID와 다릅니다.' >&2; return 1
  fi
}

pb_preflight() {
  mkdir -p "$POINTBOOK_DATA_DIR" "$POINTBOOK_BACKUP_DIR"
  for directory in "$POINTBOOK_DATA_DIR" "$POINTBOOK_BACKUP_DIR"; do
    if [ ! -w "$directory" ] || [ ! -x "$directory" ]; then
      echo '오류: 데이터 또는 백업 경로에 쓰기/탐색 권한이 없습니다.' >&2; return 1
    fi
  done
  # 실제 컨테이너 UID/GID와 mount 권한·여유 공간을 검증한다. 비밀값은 출력하지 않는다.
  pb_compose run --rm --no-deps --pull never --entrypoint python app -c \
    'from app.config import get_settings; get_settings().validate_runtime(); import pathlib,shutil,tempfile; db=pathlib.Path("/app/data/pointbook.db"); db.open("r+b").close() if db.exists() else None; dirs=[pathlib.Path("/app/data"),pathlib.Path("/app/backups")]; size=pathlib.Path("/app/data/pointbook.db").stat().st_size if pathlib.Path("/app/data/pointbook.db").exists() else 0; [(tempfile.TemporaryFile(dir=p).close(), shutil.disk_usage(p).free >= max(size*2,1048576) or (_ for _ in ()).throw(RuntimeError("insufficient space"))) for p in dirs]'
}

pb_rehearse_current_database() {
  pb_compose run --rm --no-deps --pull never --entrypoint python app -m scripts.preflight
}

pb_lock() {
  mkdir -p "$POINTBOOK_DATA_DIR"
  exec 9>"$POINTBOOK_DATA_DIR/.maintenance.lock"
  if ! flock -n 9; then echo '오류: 다른 백업/배포/복원 작업이 실행 중입니다.' >&2; return 1; fi
}

pb_wait_health() {
  local container_id status
  container_id="$(pb_compose ps -q app)"
  [ -n "$container_id" ] || { echo '오류: 앱 컨테이너가 없습니다.' >&2; return 1; }
  for _ in $(seq 1 30); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
    case "$status" in
      healthy) return 0 ;;
      unhealthy) echo '오류: DB readiness/앱 health 검사 실패.' >&2; return 1 ;;
    esac
    if [ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null)" != true ]; then
      echo '오류: 앱 컨테이너가 종료됐습니다.' >&2; return 1
    fi
    sleep 2
  done
  echo '오류: 앱 health 대기 시간 초과.' >&2; return 1
}

pb_verify() {
  if [ -z "${POINTBOOK_SELECTED_IMAGE:-}" ]; then pb_pin_current_image; fi
  pb_assert_image
  pb_wait_health
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${POINTBOOK_PORT}/health" >/dev/null
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${POINTBOOK_PORT}/login" >/dev/null
  pb_compose exec -T app python -c \
    'from pathlib import Path; from app.services.backup import validate_database; validate_database(Path("/app/data/pointbook.db")); print("DB 읽기·무결성·합계 검사 완료")'
}
