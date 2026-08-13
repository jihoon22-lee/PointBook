from sqlalchemy import create_engine, inspect, text

from app import db as db_module


def _configure(url: str) -> None:
    db_module.configure_database(url)


def test_fresh_database_migrates_to_head(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    _configure(url)
    db_module.run_migrations()

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"people", "teams", "monthly_snapshots", "balance_records", "admin_users"} <= tables
    assert "alembic_version" in tables
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_existing_database_without_version_is_stamped(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE people (id INTEGER PRIMARY KEY, personal_no VARCHAR(50),"
                " name VARCHAR(50))"
            )
        )

    _configure(url)
    db_module.run_migrations()

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None


def test_run_migrations_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'idem.db'}"
    _configure(url)
    db_module.run_migrations()
    db_module.run_migrations()

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert "people" in tables
    assert "alembic_version" in tables
