#!/usr/bin/env bash
# 이 설정의 Compose app만 중지한다. 호스트 PID나 다른 프로젝트는 건드리지 않는다.
set -euo pipefail
source "$(dirname "$0")/compose-common.sh"
pb_lock
pb_compose stop app
echo '대상 Compose 앱을 중지했습니다.'
