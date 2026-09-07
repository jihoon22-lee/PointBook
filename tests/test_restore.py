import sqlite3
from contextlib import closing

import pytest

from app.services.backup import BackupError, backup_database, recovery_status, validate_database
from scripts.restore import restore_database
from tests.test_backup import make_database


def test_restore_preserves_current_and_matches_selected_backup(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    expected = validate_database(snapshot)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("UPDATE balance_records SET amount=90,total=160")
    before = validate_database(source)
    restore_database(snapshot, source, service_stopped=True)
    assert validate_database(source) == expected
    preserved = list((tmp_path / "restore-preserved").glob("*.db"))
    assert len(preserved) == 1
    assert validate_database(preserved[0]) == before
    assert recovery_status(source)["restore"]["status"] == "verified"


def test_restore_rejects_running_or_corrupt_before_touching_target(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    before = source.read_bytes()
    with pytest.raises(BackupError, match="중지"):
        restore_database(snapshot, source)
    snapshot.write_bytes(b"corrupted")
    with pytest.raises(BackupError):
        restore_database(snapshot, source, service_stopped=True)
    assert source.read_bytes() == before
    assert not list(tmp_path.glob(".restore-*"))


def test_restore_preservation_failure_does_not_replace(tmp_path, monkeypatch):
    import scripts.restore as service

    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    expected = validate_database(source)
    monkeypatch.setattr(service, "backup_database", lambda **kwargs: None)
    with pytest.raises(BackupError, match="보존"):
        restore_database(snapshot, source, service_stopped=True)
    assert validate_database(source) == expected


def test_restore_does_not_rollback_after_verification_failure(tmp_path, monkeypatch):
    import scripts.restore as service

    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("UPDATE balance_records SET amount=90,total=160")
    original_validation = service.validate_database
    monkeypatch.setattr(
        service,
        "validate_database",
        lambda path: {} if path == source else original_validation(path),
    )
    with pytest.raises(BackupError, match="명시적 복구"):
        restore_database(snapshot, source, service_stopped=True)
    assert validate_database(source)["ledger_sums"] == [70, 30, -20, 100]
    assert len(list((tmp_path / "restore-preserved").glob("*.db"))) == 1


def test_restore_rejects_unknown_revision_before_replacing(tmp_path):
    from scripts.restore import verify_restore_backup

    source = tmp_path / "pointbook.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('unknown_future_revision')")
    snapshot = backup_database(source=source)
    assert snapshot
    with pytest.raises(BackupError, match="revision"):
        verify_restore_backup(snapshot)


def test_post_start_reconciliation_detects_loss(tmp_path):
    from scripts.restore import verify_restored_database

    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    verify_restored_database(snapshot, source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("UPDATE balance_records SET amount=90,total=160")
    with pytest.raises(BackupError, match="합계 대사"):
        verify_restored_database(snapshot, source)


def test_failed_atomic_replace_leaves_existing_wal_database_readable(tmp_path, monkeypatch):
    import scripts.restore as service

    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("UPDATE balance_records SET amount=90,total=160")
    original = service.os.replace

    def fail_staged(path, target):
        if path.name == "staged.db":
            raise OSError("synthetic replacement failure")
        original(path, target)

    monkeypatch.setattr(service.os, "replace", fail_staged)
    with pytest.raises(OSError):
        restore_database(snapshot, source, service_stopped=True)
    assert validate_database(source)["ledger_sums"] == [70, 90, -20, 160]


def test_unknown_unversioned_backup_is_rejected_before_target_change(tmp_path):
    from scripts.restore import verify_restore_backup

    source = tmp_path / "unknown.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("ALTER TABLE people ADD COLUMN unexpected TEXT")
    snapshot = backup_database(source=source)
    assert snapshot
    original = source.read_bytes()
    with pytest.raises(BackupError, match="무버전"):
        verify_restore_backup(snapshot)
    with pytest.raises(BackupError, match="무버전"):
        restore_database(snapshot, source, service_stopped=True)
    assert source.read_bytes() == original
    assert not (tmp_path / "restore-preserved").exists()


def test_migration_rehearsal_preserves_source_backup_and_global_engine(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine

    from app import db as db_module
    from scripts.restore import verify_restore_backup

    source = tmp_path / "legacy.db"
    temporary_engine = create_engine(f"sqlite:///{source}")
    cfg = db_module._alembic_config()
    with temporary_engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "b7d9f2a1c4e6")
    temporary_engine.dispose()
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    original = snapshot.read_bytes()
    active_engine, active_url = db_module.engine, db_module.current_database_url()
    metadata = verify_restore_backup(snapshot)
    assert metadata["database"]["revisions"] == ["b7d9f2a1c4e6"]
    assert snapshot.read_bytes() == original
    assert db_module.engine is active_engine and db_module.current_database_url() == active_url


def test_rehearsal_failure_does_not_preserve_or_replace_current_database(tmp_path, monkeypatch):
    from app import db as db_module

    source = tmp_path / "pointbook.db"
    make_database(source)
    snapshot = backup_database(source=source)
    assert snapshot
    original = source.read_bytes()

    def fail(engine):
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(db_module, "run_migrations", fail)
    with pytest.raises(BackupError, match="예행 검증"):
        restore_database(snapshot, source, service_stopped=True)
    assert source.read_bytes() == original
    assert not (tmp_path / "restore-preserved").exists()


def test_start_preflight_rehearses_without_mutating_current_database(tmp_path):
    from app import db as db_module
    from scripts.preflight import preflight_database

    source = tmp_path / "pointbook.db"
    make_database(source)
    original = source.read_bytes()
    active_engine = db_module.engine
    preflight_database(source)
    assert source.read_bytes() == original
    assert db_module.engine is active_engine
    assert not (tmp_path / "backups").exists()


def test_start_preflight_rejects_unknown_database_before_any_migration(tmp_path):
    from scripts.preflight import preflight_database

    source = tmp_path / "pointbook.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("ALTER TABLE people ADD COLUMN unexpected TEXT")
    original = source.read_bytes()
    with pytest.raises(RuntimeError, match="알 수 없는"):
        preflight_database(source)
    assert source.read_bytes() == original


def test_preflight_rejects_invalid_initial_admin_without_creating_database(tmp_path, monkeypatch):
    from app.config import Settings
    from scripts import preflight

    monkeypatch.setattr(
        preflight,
        "get_settings",
        lambda: Settings(app_env="production", admin_password="changeme"),
    )
    source = tmp_path / "missing.db"
    with pytest.raises(RuntimeError, match="초기 암호"):
        preflight.preflight_database(source)
    assert not source.exists()


def test_preflight_checks_stored_admin_password_before_migrating_live_database(
    tmp_path, monkeypatch
):
    from werkzeug.security import generate_password_hash

    from app.config import Settings
    from scripts import preflight

    source = tmp_path / "pointbook.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(
            "INSERT INTO admin_users (username,password_hash) VALUES ('admin',?)",
            (generate_password_hash("changeme"),),
        )
    original = source.read_bytes()
    monkeypatch.setattr(
        preflight,
        "get_settings",
        lambda: Settings(app_env="production", admin_password="custom-initial-password"),
    )
    with pytest.raises(RuntimeError, match="관리자 DB"):
        preflight.preflight_database(source)
    assert source.read_bytes() == original


def test_restore_rehearsal_rejects_admin_that_cannot_start_in_production(tmp_path, monkeypatch):
    from werkzeug.security import generate_password_hash

    from app.config import Settings
    from scripts import restore

    source = tmp_path / "pointbook.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(
            "INSERT INTO admin_users (username,password_hash) VALUES ('admin',?)",
            (generate_password_hash("changeme"),),
        )
    snapshot = backup_database(source=source)
    assert snapshot
    monkeypatch.setattr(
        restore,
        "get_settings",
        lambda: Settings(app_env="production", admin_password="custom-pass"),
    )
    with pytest.raises(BackupError, match="예행 검증"):
        restore.verify_restore_backup(snapshot)
    assert not (tmp_path / "restore-preserved").exists()
