# 팀 삭제 확인의 저장형 XSS 차단·업로드 요청 한도·보안 헤더

## 문제와 확정 범위

v1.6.1 전체 코드 리뷰(2026-10-18)에서 확인한 웹 경계 결함 두 건과 응답 헤더 보강이다.
사용자가 리뷰 결과의 구현을 요청했으며, 이 PR은 계획의 PR-A 범위만 다룬다.

- B1: 팀 목록의 삭제 확인이 팀 이름을 인라인 `onsubmit="return confirm('…')"` JS 문자열에
  넣었다. HTML 이스케이프는 속성 디코딩 뒤 사라지므로 `'`가 문자열을 탈출할 수 있었다.
  팀 이름은 월간 확정에서 요청서 행으로도 자동 생성되므로 외부 요청서가 관리자 세션의
  스크립트 실행으로 이어질 수 있었다.
- B6: 요청 본문 상한이 20MiB로 고정되어 두 파일 월간 업로드(각 `MAX_UPLOAD_MB`, 기본 10MB)가
  라우터 검증 전에 막히고, 브라우저에는 JSON 413 원문이 보였다.

R11/R13/R26 → [요구사항 #48](https://github.com/jihoon22-lee/PointBook/issues/48) 후속 수정 → 본 PR.
최종 source·merge ref CI는 PR 본문에 연결한다.

## 변경

- 인라인 확인 4곳(팀 삭제·초안 삭제·장부 반영·계산 복구)을 자동 이스케이프되는 `data-confirm`
  속성으로 바꾸고, `base.html`이 읽는 `confirm.js` 하나가 제출 버튼 또는 form의 문구로 확인한다.
  이미 자체 확인을 하는 월간 확정(`review.js`)은 `defaultPrevented`로 중복 확인하지 않는다.
- 모든 응답(정적 파일·IE 안내·413 포함)에 `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`을 붙인다. 정적 파일의 캐시 정책은 유지한다.
- 요청 본문 상한을 경로별로 둔다. `/monthly/upload`는 `max(20MiB, (2×MAX_UPLOAD_MB+1)MiB)`,
  그 밖은 기존 20MiB다. 초과 시 브라우저 폼에는 스크립트 없는 한글 안내와 돌아가기 링크,
  스크립트 요청에는 기존 `detail`과 초안 저장 화면이 읽는 `error`를 담은 JSON을 준다.
- DB 스키마·장부 계산·Excel 계약은 변경하지 않는다.

## 실제 검사

운영 환경 파일·DB 없는 worktree, Python 3.13, 명시적인 임시 `DATABASE_PATH`, Mock/합성 자료를 사용했다.

- 재현 검사 8개(`tests/test_web_boundary.py`)를 먼저 작성해 main에서 8개 모두 실패함을 확인했고,
  수정 후 8개 통과했다. 팀 이름 `x'+(localStorage.pbx=1)+'`의 확인 문구 보존, 템플릿 인라인
  보간 금지, 보안 헤더, 두 파일 한도와 설정 연동, HTML/JSON 413을 확인한다.
- 전체 서버 검사 coverage 94.26%. 전체 동시 실행에서 `test_workflow_scale[1000]`의 3초 응답
  한도가 한 번 초과했다. 단독 실행에서는 이 브랜치와 main 모두 통과했고 쿼리 수가 동일했다
  (검수 39·저장 14·대시보드 10·내보내기 6). main의 대시보드도 2.9초로 한도에 가까워
  로컬 부하에 따른 시간 변동으로 판단했다. 최종 판정은 PR CI로 확인한다.
- Ruff check/format, mypy app scripts, Alembic upgrade/check 통과.
- 구버전 Chromium 전체 E2E **28 passed**(기존 26 + 신규 2). 팀 삭제 대화상자가 문구를 그대로
  보여주고 `localStorage.pbx`가 설정되지 않음, 장부 반영은 검토 0회·반영 1회만 확인함을 본다.
  합성 Compose는 별도 프로젝트(`pb-web-hardening`)로 실행 후 정리했다.

## 한계

Win7 Chrome 109·갤럭시 실기 수용은 미실행이다. 413 안내는 브라우저가 본문 전송 중 연결을
끊은 경우 화면 대신 연결 오류로 보일 수 있다. 스크립트 CSP는 남은 인라인 `onchange`(보간 없음)
정리 후 별도로 검토한다. 운영 DB·환경·컨테이너, 버전·태그·릴리스·배포는 변경하지 않았다.
