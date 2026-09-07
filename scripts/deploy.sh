#!/usr/bin/env bash
# source 갱신 후 새 스크립트로 handoff한다. 유지보수 lock은 handoff 이후 한 번만 확보한다.
set -euo pipefail
POINTBOOK_DEPLOY_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$POINTBOOK_DEPLOY_ROOT"
export PATH="$PATH:/snap/bin"
if [ -n "$(git status --porcelain)" ]; then echo '오류: 배포 checkout에 미커밋 변경이 있습니다.' >&2; exit 1; fi
if [ -z "${POINTBOOK_DEPLOY_HANDOFF_SHA:-}" ]; then
  git fetch origin main --quiet
  POINTBOOK_DEPLOY_TARGET="$(git rev-parse --verify "${POINTBOOK_DEPLOY_REF:-origin/main}^{commit}")"
  git checkout --detach "$POINTBOOK_DEPLOY_TARGET" --quiet
  export POINTBOOK_DEPLOY_HANDOFF_SHA="$POINTBOOK_DEPLOY_TARGET"
  exec bash "$POINTBOOK_DEPLOY_ROOT/scripts/deploy.sh" "$@"
fi
POINTBOOK_DEPLOY_SHA="$(git rev-parse HEAD)"
if [ "$POINTBOOK_DEPLOY_SHA" != "$POINTBOOK_DEPLOY_HANDOFF_SHA" ]; then
  echo '오류: handoff 이후 배포 source SHA가 변경됐습니다.' >&2; exit 1
fi
unset POINTBOOK_DEPLOY_HANDOFF_SHA
source "$(dirname "$0")/compose-common.sh"
pb_lock
pb_preserve_current_image
# 검증된 이미지를 명시하면 재빌드하지 않는다. 기본은 현재 source로 빌드 후 ID 고정.
pb_select_deploy_image
if [ "$(git rev-parse HEAD)" != "$POINTBOOK_DEPLOY_SHA" ] || [ -n "$(git status --porcelain)" ]; then
  echo '오류: 이미지 선택 중 배포 source가 변경됐습니다.' >&2; exit 1
fi
pb_preflight
pb_rehearse_current_database
pb_compose stop app
trap 'echo "배포 중단: 서비스/보존 사본/이전 이미지를 확인하세요. DB 자동 rollback은 하지 않습니다." >&2' ERR
PREDEPLOY_BACKUP=""
if [ -f "$POINTBOOK_DATA_DIR/pointbook.db" ]; then
  PREDEPLOY_BACKUP="$(pb_compose run --rm --no-deps --pull never --entrypoint python app -m scripts.backup --print-path)"
fi
pb_compose up -d --no-build --pull never app
pb_verify
pb_compose restart app
pb_verify
if [ -n "$PREDEPLOY_BACKUP" ]; then
  pb_compose run --rm --no-deps --pull never --entrypoint python app \
    -m scripts.restore "$PREDEPLOY_BACKUP" --verify-current
fi
printf '%s\n' "$POINTBOOK_DEPLOY_SHA" > "$POINTBOOK_DATA_DIR/deployed-source.txt"
printf '%s\n' "$POINTBOOK_SELECTED_IMAGE" > "$POINTBOOK_DATA_DIR/deployed-image.txt"
echo "배포 완료: $POINTBOOK_DEPLOY_SHA / $POINTBOOK_SELECTED_IMAGE (http://localhost:${POINTBOOK_PORT})"
