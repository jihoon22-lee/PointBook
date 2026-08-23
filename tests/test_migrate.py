import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app import db as db_module


def _configure(url: str) -> None:
    db_module.configure_database(url)


def _upgrade_to_initial(url: str) -> None:
    _configure(url)
    command.upgrade(db_module._alembic_config(), "96588aa65d2d")


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
    _upgrade_to_initial(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))

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


def test_existing_people_upgrade_gets_unique_legacy_point_numbers(tmp_path):
    url = f"sqlite:///{tmp_path / 'existing.db'}"
    _upgrade_to_initial(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO people "
                "(id, personal_no, name, grade, status, team_id, "
                "current_carry_balance, current_amount, created_at) VALUES "
                "(1, '1001', '테스트1', '', 'active', NULL, 0, 0, CURRENT_TIMESTAMP), "
                "(2, 'S1002', '테스트2', '', 'active', NULL, 0, 0, CURRENT_TIMESTAMP)"
            )
        )

    _configure(url)
    db_module.run_migrations()

    with engine.connect() as conn:
        point_numbers = conn.execute(text("SELECT point_no FROM people ORDER BY id")).scalars()
        assert list(point_numbers) == ["L0000001", "L0000002"]


def test_point_number_schema_is_not_null_and_unique(tmp_path):
    url = f"sqlite:///{tmp_path / 'schema.db'}"
    _configure(url)
    db_module.run_migrations()
    engine = create_engine(url)
    people_columns = {column["name"]: column for column in inspect(engine).get_columns("people")}
    indexes = inspect(engine).get_indexes("people")

    assert people_columns["point_no"]["nullable"] is False
    assert people_columns["personal_no"]["nullable"] is True
    assert people_columns["account_type"]["nullable"] is False
    assert any(index["unique"] and index["column_names"] == ["point_no"] for index in indexes)


def test_personal_number_and_name_pair_can_repeat(tmp_path):
    url = f"sqlite:///{tmp_path / 'duplicates.db'}"
    _configure(url)
    db_module.run_migrations()
    engine = create_engine(url)
    values = {
        "personal_no": "S0815",
        "name": "동명이인",
        "grade": "",
        "status": "active",
        "account_type": "person",
        "current_carry_balance": 0,
        "current_amount": 0,
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO people "
                "(point_no, personal_no, name, grade, status, account_type, "
                "current_carry_balance, current_amount, created_at) VALUES "
                "(:point_no, :personal_no, :name, :grade, :status, :account_type, "
                ":current_carry_balance, :current_amount, CURRENT_TIMESTAMP)"
            ),
            [{**values, "point_no": "00000001"}, {**values, "point_no": "00000002"}],
        )


def test_point_number_is_unique_across_person_and_shared_accounts(tmp_path):
    url = f"sqlite:///{tmp_path / 'unique.db'}"
    _configure(url)
    db_module.run_migrations()
    engine = create_engine(url)
    insert = text(
        "INSERT INTO people "
        "(point_no, personal_no, name, grade, status, account_type, "
        "current_carry_balance, current_amount, created_at) VALUES "
        "(:point_no, :personal_no, :name, '', 'active', :account_type, 0, 0, CURRENT_TIMESTAMP)"
    )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert,
            [
                {
                    "point_no": "00000001",
                    "personal_no": "1001",
                    "name": "일반",
                    "account_type": "person",
                },
                {
                    "point_no": "00000001",
                    "personal_no": None,
                    "name": "공용",
                    "account_type": "shared",
                },
            ],
        )


def test_shared_account_allows_null_personal_number(tmp_path):
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    _configure(url)
    db_module.run_migrations()
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO people "
                "(point_no, personal_no, name, grade, status, account_type, "
                "current_carry_balance, current_amount, created_at) VALUES "
                "('00000001', NULL, '1팀 공용', '', 'active', 'shared', 0, 0, CURRENT_TIMESTAMP)"
            )
        )
