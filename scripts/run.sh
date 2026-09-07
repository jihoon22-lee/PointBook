#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/compose-common.sh"
pb_lock
pb_preserve_current_image
pb_select_deploy_image
pb_preflight
pb_rehearse_current_database
PRESTART_BACKUP=""
if [ -f "$POINTBOOK_DATA_DIR/pointbook.db" ]; then
  pb_compose stop app
  PRESTART_BACKUP="$(pb_compose run --rm --no-deps --pull never --entrypoint python app -m scripts.backup --print-path)"
fi
pb_compose up -d --no-build --pull never app
pb_verify
if [ -n "$PRESTART_BACKUP" ]; then
  pb_compose run --rm --no-deps --pull never --entrypoint python app \
    -m scripts.restore "$PRESTART_BACKUP" --verify-current
fi
echo "서버 시작: http://localhost:${POINTBOOK_PORT} (Docker Compose, healthy)"
