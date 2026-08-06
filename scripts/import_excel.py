"""기존 엑셀 데이터 DB 이관 스크립트.

사용법: uv run python -m scripts.import_excel --file 기존요청서.xlsx [--month 2026-07]
빈 DB에서만 실행할 수 있다 (기존 인원 유지 검증 생략).
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from app import db as db_module
from app.services.excel_import import import_excel


def main() -> None:
    parser = argparse.ArgumentParser(description="기존 엑셀 요청서 데이터를 DB로 이관")
    parser.add_argument("--file", required=True, type=Path, help="엑셀 파일 경로 (.xlsx)")
    parser.add_argument(
        "--month",
        default=datetime.now(UTC).strftime("%Y-%m"),
        help="처리 월 (YYYY-MM, 기본: 이번 달)",
    )
    args = parser.parse_args()

    db_module.configure_database(db_module.default_database_url())
    db_module.init_db()
    with db_module.SessionLocal() as db:
        result = import_excel(db, args.file, args.month)
        print(
            f"이관 완료: {result.month} 월 / 신규 인원 {result.created_persons}명, "
            f"기존 인원 {result.existing_persons}명, 잔액 기록 {result.records}건"
        )


if __name__ == "__main__":
    main()
