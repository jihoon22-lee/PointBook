# Workthrough: WSL-native project migration

**Date:** 2026-08-29

## Summary

PointBook의 tracked source를 `origin/main`에서 `/home/jihoon/projects/PointBook`으로 새로
clone하고, Git 밖의 운영 환경 파일과 SQLite 데이터만 새 ext4 작업 경로에 복원했다. 기존
`/mnt/e/projects/PointBook`은 독립 검증과 사용자 삭제 승인 전까지 원본으로 보존한다.

## Changes

### 1. Source and local state

- source와 target의 initial commit은 `b37e0fa3a5aa17970226e9cd2945ffa3ff33f116`으로 일치한다.
- `.env`는 값을 출력하지 않고 byte-for-byte 복사했으며 target mode를 `0600`으로 제한했다.
- `data/`의 SQLite 본 DB, 7개 DB 백업, import 원본과 로그를 그대로 복사하고 target directory와
  file mode를 각각 `0700`, `0600`으로 제한했다.
- `.venv`, coverage, pytest/mypy/ruff/Playwright cache는 복사하지 않고 target에서 재생성한다.

### 2. Runtime path contract

- Compose의 `${POINTBOOK_DATA_DIR:-./data}` bind는 새 repository의 `data/`를 가리킨다.
- Compose project name `pointbook`, service name, loopback port와 container UID/GID 계약은
  바뀌지 않는다. 기존 container는 새 경로에서 Compose를 다시 올릴 때 재생성한다.
- 현재 사용 가이드와 작업 규약의 실행 경로를 WSL-native target으로 갱신했다. 과거 배포
  설계서에 기록된 당시 source path는 historical evidence라 소급 수정하지 않았다.

## Testing

- source/target HEAD와 clean worktree 비교: PASS
- `.env`와 `data/` content 비교: PASS
- 본 DB와 7개 backup의 SQLite `PRAGMA quick_check`: 8/8 PASS
- target filesystem 확인: ext4
- `uv sync --frozen --group dev`: PASS(CPython 3.14.7, target `.venv` 재생성)
- `uv run ruff check .`, `uv run ruff format --check .`: PASS(77 files)
- `uv run mypy app scripts`: PASS
- `uv run pytest` 및 coverage gate: PASS(249 tests, coverage 93.61%)
- Compose runtime smoke는 기존 named volume을 새 source path에서 재연결한 뒤 최종 확인한다.

## Files Modified

- `AGENTS.md` — 현재 WSL-native 작업 경로와 Git 밖 데이터 경계
- `docs/usage-guide.md` — clone 후 실행 경로
- `workthrough/2026-08-29-wsl-project-migration.md` — 이관 경계와 검증 기록

## Notes

- `.env`, SQLite, import 원본과 로그는 ignore 상태이며 commit하지 않는다.
- 원본 프로젝트와 WSL VHD backup 삭제는 새 경로의 서비스 재기동·데이터 검증 이후 별도
  사용자 승인 사항이다.
