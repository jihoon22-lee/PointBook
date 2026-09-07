import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from scripts.backup import main


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in (
        "POINTBOOK_ENV_FILE",
        "POINTBOOK_DATA_DIR",
        "POINTBOOK_BACKUP_DIR",
        "DATABASE_PATH",
        "BACKUP_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_selected_file_and_host_mount_paths(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_PATH=wrong.db\n")
    selected = tmp_path / "selected.env"
    selected.write_text(
        "POINTBOOK_DATA_DIR=/selected/data\nPOINTBOOK_BACKUP_DIR=/protected/copies\nCOMPOSE_PROJECT_NAME=selected\n"
    )
    monkeypatch.setenv("POINTBOOK_ENV_FILE", str(selected))
    settings = get_settings()
    assert settings.database_path == "/selected/data/pointbook.db"
    assert settings.backup_dir == "/protected/copies"


def test_canonical_paths_override_host_aliases(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "DATABASE_PATH=file.db\nBACKUP_DIR=file-backups\nPOINTBOOK_DATA_DIR=/host/data\n"
    )
    monkeypatch.setenv("POINTBOOK_BACKUP_DIR", "/host/backups")
    assert get_settings().database_path == "file.db"
    assert get_settings().backup_dir == "file-backups"
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_PATH", "/app/data/pointbook.db")
    monkeypatch.setenv("BACKUP_DIR", "/app/backups")
    assert get_settings().database_path == "/app/data/pointbook.db"
    assert get_settings().backup_dir == "/app/backups"


def test_missing_selected_file_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setenv("POINTBOOK_ENV_FILE", "/absent/selected.env")
    monkeypatch.setattr("sys.argv", ["backup"])
    assert main() == 1
    assert "백업 실패" in capsys.readouterr().out


def test_unknown_app_setting_still_rejected(tmp_path):
    env_file = tmp_path / "unknown.env"
    env_file.write_text("GEMINI_API_KEEY=secret-synthetic-value\n")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=env_file)
    assert "secret-synthetic-value" not in str(exc.value)
