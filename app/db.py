from collections.abc import Generator
from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine: Engine = create_engine("sqlite://")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
_configured = False
_url: str = ""


def configure_database(url: str) -> None:
    global engine, SessionLocal, _configured, _url
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _url = url
    _configured = True


def default_database_url() -> str:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def current_database_url() -> str:
    """현재 설정된(또는 기본) DB URL. Alembic env.py가 마이그레이션 대상으로 사용한다."""
    return _url or default_database_url()


def current_database_path() -> Path:
    """현재 엔진이 가리키는 DB 파일 경로 (백업 등에서 사용)."""
    return Path(engine.url.database or "")


def ensure_default_database() -> None:
    if not _configured:
        configure_database(default_database_url())


def init_db() -> None:
    from app import models  # noqa: F401

    run_migrations()


def _alembic_config() -> Config:
    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    return cfg


def run_migrations() -> None:
    """Alembic으로 스키마를 최신(head) 상태로 만든다.

    기존(1.0.x) DB는 테이블은 있지만 alembic_version이 없으므로, 그 경우에는
    현재 상태를 head로 표식(stamp)한 뒤 마이그레이션을 적용한다.
    """
    from alembic import command
    from sqlalchemy import inspect

    cfg = _alembic_config()
    tables = set(inspect(engine).get_table_names())
    if "people" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "head")
    command.upgrade(cfg, "head")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
