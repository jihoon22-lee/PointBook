import sqlite3
from pathlib import Path

import pytest

from app import db as db_module
from app.services.ledger_import import LedgerAccount, LedgerData, LedgerRecord
from scripts import import_ledger
from tests.factories import make_person
from tests.test_ledger_import import _add_initial_observation, _business_rows


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
    db_module.run_migrations()
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


@pytest.mark.parametrize("mode", ["--dry-run", "--apply"])
@pytest.mark.parametrize("total", [0, 500000])
def test_cli_rejects_existing_observation_without_business_changes(
    monkeypatch, tmp_path, capsys, mode, total
):
    database_path = _prepare_cli(monkeypatch, tmp_path)
    with db_module.SessionLocal() as db:
        person = make_person(db, "101", "합성현재계정", point_no="00000001")
        _add_initial_observation(db, person.id, total)
        before = _business_rows(db)
    code = import_ledger.main(["--file", "source.xlsx", mode])
    assert code == 1
    assert "잔액 관측" in capsys.readouterr().out
    db_module.configure_database(f"sqlite:///{database_path}")
    with db_module.SessionLocal() as db:
        assert _business_rows(db) == before


def test_cli_serializes_analysis_and_apply_against_competing_writes(monkeypatch, tmp_path):
    database_path = _prepare_cli(monkeypatch, tmp_path)
    analyze = import_ledger.analyze_ledger_import
    apply = import_ledger.apply_ledger_import
    locked_phases = []

    def competing_write_is_blocked(phase):
        with (
            sqlite3.connect(database_path, timeout=0) as competing,
            pytest.raises(sqlite3.OperationalError, match="locked"),
        ):
            competing.execute("BEGIN IMMEDIATE")
        locked_phases.append(phase)

    def analyze_while_locked(*args, **kwargs):
        competing_write_is_blocked("analyze")
        return analyze(*args, **kwargs)

    def apply_while_locked(*args, **kwargs):
        competing_write_is_blocked("apply")
        return apply(*args, **kwargs)

    monkeypatch.setattr(import_ledger, "analyze_ledger_import", analyze_while_locked)
    monkeypatch.setattr(import_ledger, "apply_ledger_import", apply_while_locked)
    import_ledger._apply(_synthetic_data(), replace_empty_history_people=False)
    assert locked_phases == ["analyze", "apply"]
