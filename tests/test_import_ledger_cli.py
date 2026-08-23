from pathlib import Path

from app import db as db_module
from app.services.ledger_import import LedgerAccount, LedgerData, LedgerRecord
from scripts import import_ledger


def _synthetic_data() -> LedgerData:
    account = LedgerAccount(
        point_no="00000001",
        personal_no="P001",
        name="출력금지합성이름",
        grade="합성계급",
        team_name="합성팀",
        account_type="person",
        status="active",
    )
    return LedgerData(
        people=(account,),
        shared_accounts=(),
        records=(
            LedgerRecord(
                point_no=account.point_no,
                month="2024-05",
                carry_balance=0,
                amount=100,
                usage=0,
                total=100,
            ),
        ),
        months=("2024-05",),
        warnings=(),
    )


def _prepare_cli(monkeypatch, tmp_path: Path) -> Path:
    database_path = tmp_path / "cli.db"
    db_module.configure_database(f"sqlite:///{database_path}")
    db_module.Base.metadata.create_all(db_module.engine)
    monkeypatch.setattr(db_module, "default_database_url", lambda: f"sqlite:///{database_path}")
    monkeypatch.setattr(import_ledger, "parse_ledger", lambda path: _synthetic_data())
    monkeypatch.setattr(import_ledger, "validate_expected_totals", lambda data: None)
    return database_path


def test_cli_defaults_to_masked_dry_run(monkeypatch, tmp_path, capsys):
    database_path = _prepare_cli(monkeypatch, tmp_path)
    before = database_path.read_bytes()
    code = import_ledger.main(["--file", str(tmp_path / "source.xlsx")])
    output = capsys.readouterr().out
    assert code == 0
    assert "DRY-RUN" in output
    assert "출력금지합성이름" not in output
    assert "00000001" not in output
    assert database_path.read_bytes() == before


def test_cli_failed_backup_aborts_without_writes(monkeypatch, tmp_path, capsys):
    database_path = _prepare_cli(monkeypatch, tmp_path)
    before = database_path.read_bytes()
    monkeypatch.setattr(import_ledger, "backup_database", lambda: None)
    code = import_ledger.main(["--file", "source.xlsx", "--apply"])
    assert code == 1
    assert "백업" in capsys.readouterr().out
    assert database_path.read_bytes() == before
