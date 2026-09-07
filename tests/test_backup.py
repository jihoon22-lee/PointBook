import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app import db as db_module
from app.db import Base
from app.services.backup import (
    BackupError,
    backup_database,
    recovery_status,
    validate_backup,
    validate_database,
)


def make_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "INSERT INTO people (id,point_no,name,grade,status,account_type,"
            "current_carry_balance,current_amount,created_at) "
            "VALUES (1,'00123456','합성','', 'active','person',70,30,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO monthly_snapshots (id,month,created_at) VALUES (1,'2026-01',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO balance_records (snapshot_id,person_id,carry_balance,amount,usage,total) "
            "VALUES (1,1,70,30,-20,100)"
        )


def test_backup_creates_verified_file_and_private_status(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    db_module.configure_database(f"sqlite:///{source}")
    result = backup_database()
    assert result is not None
    assert result.parent == source.parent / "backups"
    metadata = validate_backup(result)
    assert metadata["database"]["ledger_sums"] == [70, 30, -20, 100]
    assert result.stat().st_mode & 0o777 == 0o600
    assert recovery_status(source)["backup"]["status"] == "verified"
    assert str(tmp_path) not in json.dumps(recovery_status(source))


def test_backup_wal_writer_and_uncommitted_changes(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    connection = sqlite3.connect(source)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE balance_records SET amount=45,total=115")
    result = backup_database(source=source)
    assert result is not None
    assert validate_backup(result)["database"]["ledger_sums"] == [70, 30, -20, 100]
    connection.commit()
    second = backup_database(source=source)
    connection.close()
    assert second is not None and second != result
    assert validate_backup(second)["database"]["ledger_sums"] == [70, 45, -20, 115]


def test_backup_during_concurrent_commits(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    ready = threading.Event()
    stop = threading.Event()

    def writer():
        connection = sqlite3.connect(source)
        ready.set()
        while not stop.is_set():
            connection.execute("UPDATE balance_records SET amount=amount+1,total=total+1")
            connection.commit()
        connection.close()

    thread = threading.Thread(target=writer)
    thread.start()
    ready.wait(5)
    try:
        result = backup_database(source=source)
        assert result is not None
        sums = validate_backup(result)["database"]["ledger_sums"]
        assert sums[0] + sums[1] == sums[3]
    finally:
        stop.set()
        thread.join(5)


def test_prune_only_verified_owned_backups(tmp_path):
    source = tmp_path / "one" / "pointbook.db"
    other = tmp_path / "two" / "pointbook.db"
    make_database(source)
    make_database(other)
    directory = tmp_path / "protected"
    foreign = backup_database(source=other, directory=directory, keep=1)
    first = backup_database(source=source, directory=directory, keep=1)
    second = backup_database(source=source, directory=directory, keep=1)
    assert first and second and foreign
    assert not first.exists()
    assert second.exists() and foreign.exists()
    assert len(list(directory.glob("*.db"))) == 2
    second.with_suffix(".json").write_text("{}")
    third = backup_database(source=source, directory=directory, keep=1)
    assert third and third.exists() and second.exists()


@pytest.mark.parametrize("keep", [0, -1, 10001])
def test_invalid_keep_rejected_without_removing_old(tmp_path, keep):
    source = tmp_path / "pointbook.db"
    make_database(source)
    old = backup_database(source=source)
    with pytest.raises(BackupError, match="보관"):
        backup_database(source=source, keep=keep)
    assert old and old.exists()


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_copy_failure_preserves_existing_and_removes_temporary(tmp_path, monkeypatch, failure):
    import app.services.backup as service

    source = tmp_path / "pointbook.db"
    make_database(source)
    old = backup_database(source=source, keep=1)

    def fail(*args, **kwargs):
        raise failure("synthetic failure")

    monkeypatch.setattr(service, "copy_database", fail)
    with pytest.raises(failure):
        backup_database(source=source, keep=1)
    assert old and old.exists()
    assert not list(old.parent.glob(".backup-*"))
    assert len(list(old.parent.glob("*.db"))) == 1
    assert recovery_status(source)["backup"]["status"] == "failed"


def test_insufficient_space_and_corrupt_database(tmp_path, monkeypatch):
    import app.services.backup as service

    source = tmp_path / "pointbook.db"
    make_database(source)
    usage = service.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(service.shutil, "disk_usage", lambda _: usage._replace(free=0))
    with pytest.raises(BackupError, match="여유 공간"):
        backup_database(source=source)
    source.write_bytes(b"not sqlite")
    with pytest.raises(BackupError):
        validate_database(source)


def test_missing_and_non_pointbook_database(tmp_path):
    source = tmp_path / "missing.db"
    assert backup_database(source=source) is None
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    with pytest.raises(BackupError, match="최소 DB 구조"):
        backup_database(source=source)


def test_corrupt_backup_metadata_and_formula_are_rejected(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    backup = backup_database(source=source)
    assert backup
    with closing(sqlite3.connect(backup)) as connection, connection:
        connection.execute("UPDATE balance_records SET total=999")
    with pytest.raises(BackupError, match="해시"):
        validate_backup(backup)
    with pytest.raises(BackupError, match="공식"):
        validate_database(backup)


def test_backup_owner_survives_directory_move_and_prunes_same_ledger(tmp_path):
    source = tmp_path / "before-move" / "pointbook.db"
    directory = tmp_path / "protected"
    make_database(source)
    first = backup_database(source=source, directory=directory, keep=1)
    assert first
    owner = validate_backup(first)["owner"]
    moved = tmp_path / "after-move"
    source.parent.rename(moved)
    second = backup_database(source=moved / source.name, directory=directory, keep=1)
    assert second and validate_backup(second)["owner"] == owner
    assert second.exists() and not first.exists()


def test_corrupt_backup_owner_cannot_delete_existing_backups(tmp_path):
    source = tmp_path / "pointbook.db"
    make_database(source)
    original = backup_database(source=source, keep=1)
    assert original
    (source.parent / ".pointbook.db.backup-owner").write_text("not-a-uuid")
    with pytest.raises(BackupError, match="소유 식별자"):
        backup_database(source=source, keep=1)
    assert original.exists()
