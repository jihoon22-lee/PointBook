#!/usr/bin/env bash
# 호스트 cron/systemd timer의 단일 실행 진입점. 스케줄 설치는 운영자가 수행한다.
set -euo pipefail
source "$(dirname "$0")/compose-common.sh"
pb_lock
pb_pin_current_image
pb_preflight
pb_compose run --rm --no-deps --pull never --entrypoint python app -m scripts.backup
