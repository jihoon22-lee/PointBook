from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config
from sqlalchemy import (
    CheckConstraint,
    Connection,
    Engine,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine: Engine = create_engine("sqlite://")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
_configured = False
_url: str = ""
INITIAL_SCHEMA_REVISION = "96588aa65d2d"


def configure_database(url: str) -> None:
    global engine, SessionLocal, _configured, _url
    engine.dispose()
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

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


@contextmanager
def migration_transaction(connection: Connection) -> Generator[Connection, None, None]:
    """호출자의 열린 transaction은 소유하지 않는다. 자체 transaction만 FK 조정한다."""
    if connection.in_transaction():
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise RuntimeError("기존 DB에 외래키 위반이 있어 이전을 중단했습니다.")
        yield connection
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise RuntimeError("이전 후 외래키 검사가 실패했습니다.")
        return
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        connection.rollback()
        raise RuntimeError("기존 DB에 외래키 위반이 있어 이전을 중단했습니다.")
    connection.commit()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield connection
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise RuntimeError("이전 후 외래키 검사가 실패했습니다.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar() != 1:
            raise RuntimeError("마이그레이션 연결의 외래키 재활성화에 실패했습니다.")
        connection.commit()


def run_migrations(bind: Engine | Connection | None = None) -> None:
    """격리된 bind에서도 stamp·upgrade·스키마 검증을 하나의 transaction으로 실행한다."""
    from alembic import command
    from alembic.script import ScriptDirectory

    from app import models  # noqa: F401

    target = bind if bind is not None else engine
    if isinstance(target, Engine):
        with target.connect() as connection:
            run_migrations(connection)
        return
    cfg = _alembic_config()
    cfg.attributes["connection"] = target
    with migration_transaction(target):
        tables = set(inspect(target).get_table_names())
        if tables and "alembic_version" not in tables:
            revision = _known_unversioned_revision(target)
            if revision is None:
                raise RuntimeError("알 수 없는 DB 스키마입니다. 백업과 스키마 검토가 필요합니다.")
            command.stamp(cfg, revision)
        elif "alembic_version" in tables:
            revisions = list(
                target.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
            if revisions == [ScriptDirectory.from_config(cfg).get_current_head()]:
                validate_schema(target)
            elif (
                len(revisions) == 1
                and revisions[0] in {INITIAL_SCHEMA_REVISION, "b7d9f2a1c4e6", "c14a04000001"}
                and _known_unversioned_revision(target, allow_extra_tables=True) != revisions[0]
            ):
                raise RuntimeError("DB revision과 실제 스키마가 일치하지 않습니다.")
        command.upgrade(cfg, "head")
        validate_schema(target)


def validate_schema(bind: Engine | Connection | None = None, *, full: bool = True) -> None:
    """현재 앱 스키마 확인. full=False는 health용 컬럼 접근만 수행한다."""
    from app import models  # noqa: F401

    target = bind if bind is not None else engine
    if isinstance(target, Engine):
        with target.connect() as connection:
            validate_schema(connection, full=full)
        return
    if not full:
        for table in Base.metadata.sorted_tables:
            target.execute(select(*table.c).limit(0)).close()
        return
    inspector = inspect(target)
    if not set(Base.metadata.tables) <= set(inspector.get_table_names()):
        raise RuntimeError("앱 필수 DB 테이블이 없습니다.")
    for name, table in Base.metadata.tables.items():
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError("앱 DB 컬럼이 현재 스키마와 일치하지 않습니다.")
        for column in table.c:
            actual = columns[column.name]
            if str(actual["type"]) != str(column.type) or actual["nullable"] != column.nullable:
                raise RuntimeError("앱 DB 컬럼 타입·NULL 계약이 일치하지 않습니다.")
        if inspector.get_pk_constraint(name)["constrained_columns"] != [
            c.name for c in table.primary_key.columns
        ]:
            raise RuntimeError("앱 DB 기본키가 일치하지 않습니다.")
        expected_unique = {
            tuple(c.name for c in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        expected_unique.update(
            tuple(c.name for c in index.columns) for index in table.indexes if index.unique
        )
        actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
        actual_unique.update(
            tuple(str(c) for c in index["column_names"])
            for index in inspector.get_indexes(name)
            if index["unique"]
        )
        if expected_unique != actual_unique:
            raise RuntimeError("앱 DB 고유키가 일치하지 않습니다.")
        expected_fks = {
            (
                tuple(c.parent.name for c in fk.elements),
                fk.referred_table.name,
                tuple(c.column.name for c in fk.elements),
            )
            for fk in table.foreign_key_constraints
        }
        actual_fks = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys(name)
        }
        if expected_fks != actual_fks:
            raise RuntimeError("앱 DB 외래키가 일치하지 않습니다.")
        expected_checks = {
            c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
        }
        actual_checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(name)}
        if expected_checks != actual_checks:
            raise RuntimeError("앱 DB 도메인 제약이 일치하지 않습니다.")


def _known_unversioned_revision(
    bind: Engine | Connection | None = None, *, allow_extra_tables: bool = False
) -> str | None:
    """테이블·컬럼·타입·NULL·키를 대조한다. 컬럼 하나로 head를 추정하지 않는다."""
    from sqlalchemy import inspect

    inspector = inspect(bind if bind is not None else engine)
    expected = {
        "teams": {"id", "name", "color"},
        "admin_users": {"id", "username", "password_hash"},
        "monthly_snapshots": {"id", "month", "created_at"},
        "balance_records": {
            "id",
            "snapshot_id",
            "person_id",
            "carry_balance",
            "amount",
            "usage",
            "total",
        },
        "people": {
            "id",
            "personal_no",
            "name",
            "grade",
            "status",
            "team_id",
            "current_carry_balance",
            "current_amount",
            "created_at",
        },
    }
    actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
    if (
        (not set(expected) <= actual_tables)
        if allow_extra_tables
        else (actual_tables != set(expected))
    ):
        return None
    columns = {table: {c["name"]: c for c in inspector.get_columns(table)} for table in expected}
    modern = "point_no" in columns["people"]
    hardened = "auth_version" in columns["admin_users"]
    if hardened:
        if not modern:
            return None
        expected["admin_users"].add("auth_version")
    if modern:
        expected["people"].update({"point_no", "account_type"})
    integers = {
        "id",
        "snapshot_id",
        "person_id",
        "team_id",
        "carry_balance",
        "amount",
        "usage",
        "total",
        "current_carry_balance",
        "current_amount",
        "auth_version",
    }
    for table, names in expected.items():
        if set(columns[table]) != names:
            return None
        for name, column in columns[table].items():
            nullable = name == "team_id" or (modern and table == "people" and name == "personal_no")
            if bool(column["nullable"]) != nullable:
                return None
            lengths = {
                "point_no": 8,
                "month": 7,
                "password_hash": 200,
                "color": 20,
                "status": 20,
                "account_type": 20,
            }
            kind = str(column["type"])
            expected_type = (
                "INTEGER"
                if name in integers
                else "DATETIME"
                if name == "created_at"
                else f"VARCHAR({lengths.get(name, 50)})"
            )
            if kind != expected_type:
                return None
        if inspector.get_pk_constraint(table)["constrained_columns"] != ["id"]:
            return None
    unique = {
        "teams": {("name",)},
        "admin_users": {("username",)},
        "monthly_snapshots": {("month",)},
        "balance_records": {("snapshot_id", "person_id")},
        "people": {("point_no",)} if modern else {("personal_no", "name")},
    }
    for table, keys in unique.items():
        actual = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table)}
        actual.update(
            tuple(str(name) for name in c["column_names"])
            for c in inspector.get_indexes(table)
            if c["unique"]
        )
        if actual != keys:
            return None
    expected_fks = {
        "people": {(("team_id",), "teams", ("id",))},
        "balance_records": {
            (("person_id",), "people", ("id",)),
            (("snapshot_id",), "monthly_snapshots", ("id",)),
        },
    }
    for table in expected:
        actual_fks = {
            (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
            for f in inspector.get_foreign_keys(table)
        }
        if actual_fks != expected_fks.get(table, set()):
            return None
    expected_checks = {
        "people": {
            "ck_people_status": "status IN ('active', 'inactive')",
            "ck_people_account_type": "account_type IN ('person', 'shared')",
            "ck_people_shared_active": "account_type != 'shared' OR status = 'active'",
            "ck_people_carry": "current_carry_balance >= 0 AND typeof(current_carry_balance) = 'integer'",
            "ck_people_amount": "current_amount >= 0 AND typeof(current_amount) = 'integer'",
        },
        "balance_records": {
            "ck_records_carry": "carry_balance >= 0 AND typeof(carry_balance) = 'integer'",
            "ck_records_amount": "amount >= 0 AND typeof(amount) = 'integer'",
        },
    }
    for table in expected:
        actual_checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(table)}
        if actual_checks != (expected_checks.get(table, {}) if hardened else {}):
            return None
    if hardened:
        return "c14a04000001"
    return "b7d9f2a1c4e6" if modern else INITIAL_SCHEMA_REVISION


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
