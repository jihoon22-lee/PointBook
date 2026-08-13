"""DB 수동 백업 스크립트.

사용법: uv run python -m scripts.backup
"""

from app import db as db_module
from app.services.backup import backup_database


def main() -> None:
    db_module.configure_database(db_module.default_database_url())
    result = backup_database()
    if result is None:
        print("백업할 DB 파일이 없습니다.")
    else:
        print(f"백업 완료: {result}")


if __name__ == "__main__":
    main()
