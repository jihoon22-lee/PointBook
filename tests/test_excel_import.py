import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models import MonthlySnapshot, Person, Team
from app.services.excel_import import import_excel, read_rows
from tests.factories import make_person


def _make_xlsx(path, rows, header=None):
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    if header:
        ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_read_rows_with_headers(tmp_path):
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [
            ("1팀", "김소방", "소방경", 50000, "101", "", 10000),
            ("2팀", "이소방", "소방위", 30000, "102", "비고", 5000),
        ],
        header=("팀", "이름", "계급", "금액", "개인번호", "비고", "잔액"),
    )
    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[0].team == "1팀"
    assert rows[0].amount == 50000
    assert rows[0].balance == 10000
    assert rows[1].note == "비고"


def test_read_rows_synonym_headers(tmp_path):
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [("구조대", "김소방", "소방경", 50000, "101")],
        header=("부서", "성명", "직급", "충전금액", "번호"),
    )
    rows = read_rows(path)
    assert rows[0].team == "구조대"
    assert rows[0].personal_no == "101"


def test_read_rows_skips_rows_without_key_fields(tmp_path):
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [("1팀", "김소방", "소방경", 50000, "101"), ("", "", "", "", "")],
        header=("팀", "이름", "계급", "금액", "개인번호"),
    )
    rows = read_rows(path)
    assert len(rows) == 1


def test_import_excel_creates_teams_persons_snapshot(tmp_path, client, db):
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [("1팀", "김소방", "소방경", 50000, "101", "", 10000)],
        header=("팀", "이름", "계급", "금액", "개인번호", "비고", "잔액"),
    )
    result = import_excel(db, path, "2026-07")
    assert result.created_persons == 1
    assert result.records == 1
    person = db.scalar(select(Person).where(Person.personal_no == "101"))
    assert person is not None
    assert person.team.name == "1팀"
    assert person.status == "active"
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07"))
    assert snapshot is not None
    record = snapshot.records[0]
    assert record.carry_balance == 10000
    assert record.total == 60000


def test_import_excel_rejects_nonempty_db(tmp_path, client, db):
    make_person(db, "999", "기존인원")
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [("1팀", "김소방", "소방경", 50000, "101")],
        header=("팀", "이름", "계급", "금액", "개인번호"),
    )
    with pytest.raises(ValueError):
        import_excel(db, path, "2026-07")


def test_import_excel_rejects_duplicate_month(tmp_path, client, db):
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [("1팀", "김소방", "소방경", 50000, "101")],
        header=("팀", "이름", "계급", "금액", "개인번호"),
    )
    import_excel(db, path, "2026-07")
    with pytest.raises(ValueError):
        import_excel(db, path, "2026-07")


def test_import_excel_team_created_once(tmp_path, client, db):
    path = tmp_path / "req.xlsx"
    _make_xlsx(
        path,
        [("1팀", "김소방", "소방경", 50000, "101"), ("1팀", "이소방", "소방위", 30000, "102")],
        header=("팀", "이름", "계급", "금액", "개인번호"),
    )
    import_excel(db, path, "2026-07")
    teams = list(db.scalars(select(Team)).all())
    assert len(teams) == 1
