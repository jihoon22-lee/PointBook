from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine: Engine = create_engine("sqlite://")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
_configured = False


def configure_database(url: str) -> None:
    global engine, SessionLocal, _configured
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _configured = True


def default_database_url() -> str:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def ensure_default_database() -> None:
    if not _configured:
        configure_database(default_database_url())


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    migrate(engine)


def migrate(db_engine: Engine) -> None:
    """스키마 변경분을 기존 DB에 적용한다 (신규 컬럼 추가)."""
    from sqlalchemy import text

    with db_engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(people)"))}
        if "current_carry_balance" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE people ADD COLUMN current_carry_balance INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "current_amount" not in columns:
            conn.execute(
                text("ALTER TABLE people ADD COLUMN current_amount INTEGER NOT NULL DEFAULT 0")
            )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
