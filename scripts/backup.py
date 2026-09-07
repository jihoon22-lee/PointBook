"""수동/호스트 스케줄 백업 CLI. 실패는 비영 종료하며 민감 예외를 출력하지 않는다."""

import argparse
from pathlib import Path

from app import db as db_module
from app.services.backup import backup_database


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 검증 백업")
    parser.add_argument("--directory", type=Path, help="별도 보호된 백업 경로")
    parser.add_argument(
        "--print-path", action="store_true", help="검증 사본 경로만 반환(배포 대사용)"
    )
    args = parser.parse_args()
    try:
        db_module.configure_database(db_module.default_database_url())
        result = backup_database(directory=args.directory)
        if result is None:
            print("백업할 DB 파일이 없습니다.")
            return 1
        print(str(result) if args.print_path else f"검증 백업 완료: {result.name}")
        return 0
    except Exception:  # noqa: BLE001 - DB·환경의 민감 예외 원문을 공개하지 않는다.
        print("백업 실패: 경로 권한·공간·DB 검증 상태를 확인하세요.")
        return 1
    finally:
        db_module.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
