import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app import db as db_module


@pytest.fixture(autouse=True)
def _dispose_test_engines(monkeypatch):
    # Verification engines are independent of app lifespan and must also close.
    engines = []
    original = create_engine

    def tracked_engine(*args, **kwargs):
        engine = original(*args, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(__name__ + ".create_engine", tracked_engine)
    yield
    for engine in engines:
        engine.dispose()
    db_module.engine.dispose()


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


def test_new_connections_always_enforce_foreign_keys(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'fk.db'}")
    db_module.run_migrations()
    for _ in range(2):
        with db_module.engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        db_module.engine.dispose()
    with pytest.raises(IntegrityError), db_module.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO balance_records (snapshot_id, person_id, carry_balance, amount, usage, total) VALUES (999, 999, 0, 0, -1, 0)"
            )
        )


def test_unknown_unversioned_schema_is_not_stamped(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'unknown.db'}")
    with db_module.engine.begin() as conn:
        conn.execute(text("CREATE TABLE people (id INTEGER PRIMARY KEY, point_no VARCHAR(8))"))
    with pytest.raises(RuntimeError, match="알 수 없는"):
        db_module.run_migrations()
    assert inspect(db_module.engine).get_table_names() == ["people"]


def test_known_point_schema_without_version_uses_its_revision(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'point-schema.db'}")
    command.upgrade(db_module._alembic_config(), "b7d9f2a1c4e6")
    with db_module.engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
    db_module.run_migrations()
    assert "auth_version" in {
        c["name"] for c in inspect(db_module.engine).get_columns("admin_users")
    }


def test_migration_rejects_existing_fk_violation_without_repair(tmp_path):
    url = f"sqlite:///{tmp_path / 'orphans.db'}"
    _configure(url)
    command.upgrade(db_module._alembic_config(), "b7d9f2a1c4e6")
    external = create_engine(url)
    try:
        with external.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO balance_records (snapshot_id, person_id, carry_balance, amount, usage, total) VALUES (999, 999, 0, 0, -5, 0)"
                )
            )
        with pytest.raises(RuntimeError, match="외래키"):
            db_module.run_migrations()
        with external.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM balance_records")).scalar() == 1
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "b7d9f2a1c4e6"
            )
    finally:
        external.dispose()


def test_failed_domain_migration_is_atomic_and_preserves_values(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'invalid-domain.db'}")
    command.upgrade(db_module._alembic_config(), "b7d9f2a1c4e6")
    with db_module.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO people (point_no, name, grade, status, account_type, current_carry_balance, current_amount, created_at) VALUES ('00000001', '합성 공용', '', 'inactive', 'shared', 0, 0, CURRENT_TIMESTAMP)"
            )
        )
    with pytest.raises(RuntimeError, match="도메인"):
        db_module.run_migrations()
    assert "auth_version" not in {
        c["name"] for c in inspect(db_module.engine).get_columns("admin_users")
    }
    with db_module.engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM people")).scalar() == "inactive"
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "b7d9f2a1c4e6"
        )
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_domain_checks_preserve_negative_usage(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'negative-usage.db'}")
    db_module.run_migrations()
    with db_module.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO people (id, point_no, name, grade, status, account_type, current_carry_balance, current_amount, created_at) VALUES (1, '00000001', '합성 공용', '', 'active', 'shared', 0, 0, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO monthly_snapshots (id, month, created_at) VALUES (1, '2026-01', CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO balance_records (snapshot_id, person_id, carry_balance, amount, usage, total) VALUES (1, 1, 200, 0, -100, 200)"
            )
        )
    with pytest.raises(IntegrityError), db_module.engine.begin() as conn:
        conn.execute(text("UPDATE balance_records SET carry_balance = -1"))
    with pytest.raises(IntegrityError), db_module.engine.begin() as conn:
        conn.execute(text("UPDATE people SET status = 'inactive'"))
    with db_module.engine.connect() as conn:
        assert conn.execute(text("SELECT usage FROM balance_records")).scalar() == -100


def test_exact_unversioned_current_schema_has_concrete_revision(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'current-unversioned.db'}")
    command.upgrade(db_module._alembic_config(), "c14a04000001")
    with db_module.engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
    assert db_module._known_unversioned_revision() == "c14a04000001"
    db_module.run_migrations()


def test_altered_known_shape_is_not_stamped(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'altered-known.db'}")
    command.upgrade(db_module._alembic_config(), "b7d9f2a1c4e6")
    with db_module.engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("ALTER TABLE teams ADD COLUMN unknown VARCHAR(20)"))
    with pytest.raises(RuntimeError, match="알 수 없는"):
        db_module.run_migrations()
    assert "alembic_version" not in inspect(db_module.engine).get_table_names()


def test_external_engine_rehearsal_does_not_reconfigure_global(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'untouched-global.db'}")
    global_engine = db_module.engine
    temporary = create_engine(f"sqlite:///{tmp_path / 'rehearsal.db'}")
    db_module.run_migrations(temporary)
    db_module.validate_schema(temporary)
    assert db_module.engine is global_engine
    assert inspect(global_engine).get_table_names() == []
    assert "balance_records" in inspect(temporary).get_table_names()


def test_alembic_uses_supplied_connection_and_preserves_transaction(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'global-isolation.db'}")
    temporary = create_engine(f"sqlite:///{tmp_path / 'supplied.db'}")
    with temporary.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        cfg = db_module._alembic_config()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        db_module.validate_schema(conn)
        assert conn.in_transaction()
        conn.rollback()
        assert inspect(conn).get_table_names() == []
    assert inspect(db_module.engine).get_table_names() == []


def test_legacy_schema_classifier_accepts_external_bind(tmp_path):
    _configure(f"sqlite:///{tmp_path / 'classifier-global.db'}")
    temporary = create_engine(f"sqlite:///{tmp_path / 'classifier-external.db'}")
    with temporary.connect() as conn:
        cfg = db_module._alembic_config()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "b7d9f2a1c4e6")
        assert db_module._known_unversioned_revision(conn) == "b7d9f2a1c4e6"
        conn.commit()
    assert db_module._known_unversioned_revision(temporary) == "b7d9f2a1c4e6"
    assert inspect(db_module.engine).get_table_names() == []


def test_unversioned_failed_upgrade_does_not_leave_stamp(tmp_path):
    temporary = create_engine(f"sqlite:///{tmp_path / 'bad-unversioned.db'}")
    with temporary.connect() as conn:
        cfg = db_module._alembic_config()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "b7d9f2a1c4e6")
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(
            text(
                "INSERT INTO people (point_no, name, grade, status, account_type, current_carry_balance, current_amount, created_at) VALUES ('00000001', '합성', '', 'inactive', 'shared', 0, 0, CURRENT_TIMESTAMP)"
            )
        )
        conn.commit()
    with pytest.raises(RuntimeError, match="도메인"):
        db_module.run_migrations(temporary)
    assert "alembic_version" not in inspect(temporary).get_table_names()
    assert "auth_version" not in {c["name"] for c in inspect(temporary).get_columns("admin_users")}


def test_history_migration_preserves_every_financial_row_and_marks_unknown(tmp_path):
    import json

    url = f"sqlite:///{tmp_path / 'history.db'}"
    _configure(url)
    command.upgrade(db_module._alembic_config(), "c14a04000001")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO people (id,point_no,personal_no,name,grade,status,account_type,team_id,current_carry_balance,current_amount,created_at) VALUES (1,'00000101','101','합성인원','','inactive','person',NULL,150,20,CURRENT_TIMESTAMP),(2,'00000102',NULL,'합성공용','','active','shared',NULL,50,10,CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO monthly_snapshots (id,month,created_at) VALUES (1,'2026-01',CURRENT_TIMESTAMP),(2,'2026-03',CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO balance_records (id,snapshot_id,person_id,carry_balance,amount,usage,total) VALUES (1,1,1,0,100,0,100),(2,2,1,150,20,-50,170),(3,2,2,50,10,0,60)"
            )
        )
        before_people = conn.execute(text("SELECT * FROM people ORDER BY id")).all()
        before_months = conn.execute(text("SELECT * FROM monthly_snapshots ORDER BY id")).all()
        before_records = conn.execute(text("SELECT * FROM balance_records ORDER BY id")).all()
    db_module.run_migrations()
    with engine.connect() as conn:
        assert [
            tuple(row[: len(before_people[0])])
            for row in conn.execute(text("SELECT * FROM people ORDER BY id"))
        ] == [tuple(row) for row in before_people]
        assert [
            tuple(row[: len(before_months[0])])
            for row in conn.execute(text("SELECT * FROM monthly_snapshots ORDER BY id"))
        ] == [tuple(row) for row in before_months]
        assert [
            tuple(row[: len(before_records[0])])
            for row in conn.execute(text("SELECT * FROM balance_records ORDER BY id"))
        ] == [tuple(row) for row in before_records]
        histories = conn.execute(
            text("SELECT provenance,observed_at,profile_data FROM balance_records")
        ).all()
        assert all(
            row.provenance == "master_at_migration" and row.observed_at is None for row in histories
        )
        assert json.loads(histories[0].profile_data)["status"] == "inactive"
        revisions = conn.execute(
            text("SELECT operation_id,source,data_json FROM balance_revisions ORDER BY record_id")
        ).all()
        assert len(revisions) == 3
        assert all(
            row.operation_id is None and row.source == "migration_baseline" for row in revisions
        )
        assert json.loads(revisions[1].data_json)["usage"] == -50
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
