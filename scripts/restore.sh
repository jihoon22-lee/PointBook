#!/usr/bin/env bash
# 사용법: 동일 POINTBOOK_* 환경에서 scripts/restore.sh /보호경로/백업.db
set -euo pipefail
source "$(dirname "$0")/compose-common.sh"
if [ "$#" -ne 1 ]; then echo '사용법: scripts/restore.sh 백업.db' >&2; exit 2; fi
RESTORE_SOURCE="$(realpath "$1")"
RESTORE_FOLDER="$(dirname "$RESTORE_SOURCE")"
RESTORE_NAME="$(basename "$RESTORE_SOURCE")"
pb_lock
pb_pin_current_image
pb_preflight
# 기동 스크립트를 실행하지 않고 선택 사본을 읽기 전용 mount로 검증한다.
pb_compose run --rm --no-deps --pull never -v "$RESTORE_FOLDER:/restore:ro" --entrypoint python app \
  -m scripts.restore "/restore/$RESTORE_NAME"
pb_compose stop app
trap 'echo "복원 중단: 기존 보존 사본과 서비스 상태를 확인하세요. 쓰기 재개 후 자동 rollback 금지." >&2' ERR
pb_compose run --rm --no-deps --pull never -v "$RESTORE_FOLDER:/restore:ro" --entrypoint python app \
  -m scripts.restore "/restore/$RESTORE_NAME" --apply --service-stopped
pb_compose up -d --no-build --pull never app
pb_verify
pb_compose restart app
pb_verify
pb_compose run --rm --no-deps --pull never -v "$RESTORE_FOLDER:/restore:ro" --entrypoint python app \
  -m scripts.restore "/restore/$RESTORE_NAME" --verify-current
echo '복원·앱 읽기·장부 대사·재시작 영속성 확인 완료.'
