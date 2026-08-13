import sqlite3

from app import db as db_module
from app.services.backup import backup_database


def _make_db(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()


def test_backup_creates_file(tmp_path):
    db_path = tmp_path / "db" / "pointbook.db"
    _make_db(db_path)
    db_module.configure_database(f"sqlite:///{db_path}")
    result = backup_database()
    assert result is not None
    assert result.exists()
    assert result.parent == db_path.parent / "backups"


def test_backup_prunes_old(tmp_path, monkeypatch):
    import app.services.backup as backup_mod
    from app.config import Settings

    monkeypatch.setattr(backup_mod, "get_settings", lambda: Settings(backup_keep=1))
    db_path = tmp_path / "db" / "pointbook.db"
    _make_db(db_path)
    db_module.configure_database(f"sqlite:///{db_path}")
    backup_database()
    backup_database()
    backups = list((db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 1
