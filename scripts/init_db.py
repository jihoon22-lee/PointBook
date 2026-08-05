"""DB 초기화 스크립트 — 테이블 생성 및 관리자 계정 생성.

사용법: uv run python -m scripts.init_db
"""

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app import db as db_module
from app.config import get_settings
from app.models import AdminUser


def ensure_admin() -> str:
    settings = get_settings()
    with db_module.SessionLocal() as db:
        existing = db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
        if existing is not None:
            return f"관리자 계정 '{settings.admin_username}' 이(가) 이미 존재합니다."
        db.add(
            AdminUser(
                username=settings.admin_username,
                password_hash=generate_password_hash(settings.admin_password),
            )
        )
        db.commit()
        return f"관리자 계정 '{settings.admin_username}' 생성 완료."


def main() -> None:
    db_module.configure_database(db_module.default_database_url())
    db_module.init_db()
    print(ensure_admin())


if __name__ == "__main__":
    main()
