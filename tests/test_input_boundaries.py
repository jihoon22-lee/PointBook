"""입력 경계: 빈 계정 구분, 여러 줄 비고 붙여넣기, 기존 Excel 이관, 포인트번호 정규화."""

import json
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app import db as db_module
from app.ai.rows import parse_rows
from app.models import BalanceRecord, MonthlySnapshot, Person, Team
from app.services.excel_import import import_excel, read_rows
from app.services.paired_images import merge_roster
from app.services.parsing import parse_pasted_raw
from app.services.xlsx import read_request
from tests.factories import make_person
from tests.monthly_helpers import review_fields
from tests.test_legacy_status import legacy_record
from tests.test_xlsx import workbook_data

# ---------- 빈 계정 구분은 일반 인원 ----------


def _ai_row(**overrides):
    return {
        "point_no": "",
        "personal_no": "0011",
        "name": "합성이름",
        "team": "합성팀",
        "grade": "소방경",
        "amount": "100",
        "note": "",
        "account_type": "",
        **overrides,
    }


@pytest.mark.parametrize("value", ["", None, "  "])
def test_ai_blank_account_type_is_person(value):
    rows = parse_rows(json.dumps([_ai_row(account_type=value)], ensure_ascii=False))
    assert rows[0].account_type == "person"
    assert rows[0].source_issue == ""


def test_ai_invalid_account_type_still_reported():
    rows = parse_rows(
        json.dumps([_ai_row(point_no="00000011", account_type="기타")], ensure_ascii=False)
    )
    assert "계정 유형" in rows[0].source_issue


def test_paired_roster_fills_personal_number_for_blank_ai_type():
    payment = parse_rows(json.dumps([_ai_row(personal_no="")], ensure_ascii=False))
    roster = parse_rows(
        json.dumps([_ai_row(account_type="person", amount="")], ensure_ascii=False), roster=True
    )
    assert merge_roster(payment, roster).rows[0].personal_no == "0011"


def test_headered_paste_blank_account_type_is_person():
    rows = parse_pasted_raw(
        "계정 구분\t팀\t이름\t계급\t충전액\t개인번호\n\t합성팀\t합성이름\t소방경\t100\t0011\n"
        "공용\t\t합성공용\t\t0\t\n"
    )
    assert [row.account_type for row in rows] == ["person", "shared"]


def test_excel_v2_blank_account_type_auto_links(auth_client, db):
    make_person(db, "0011", "합성이름", grade="소방경")
    data = workbook_data(
        version="2", rows=[[1, None, "", "합성이름", "소방경", 100, "0011", "", 0]]
    )
    assert read_request(data, "request.xlsx").rows[0].account_type == "person"
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-08"}, files={"file": ("r.xlsx", data)}
    )
    assert review_fields(response)["link_state_0"].startswith("auto:")


# ---------- 포인트번호 정규화는 normalize_point_no 하나로 ----------


def test_paste_point_with_nbsp_is_recognized_as_shared(auth_client, db):
    make_person(db, "", "합성공용", point_no="00000003", account_type="shared")
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-08", "pasted": "\t합성공용\t\t0\t\t0000 0003"},
    )
    assert review_fields(response)["account_type_0"] == "shared"


def test_previous_total_shown_for_point_with_nbsp(auth_client, db):
    person = make_person(db, "0021", "합성직전")
    legacy_record(db, person, "2026-06", 700)
    db.commit()
    response = auth_client.post(
        "/monthly/review",
        data={
            "month": "2026-07",
            "point_no_0": "0000 0021",
            "account_type_0": "person",
            "personal_no_0": "0021",
            "name_0": "합성직전",
            "team_0": "",
            "grade_0": "소방위",
            "amount_0": "100",
            "carry_0": "",
        },
    )
    assert 'data-previous-total="700"' in response.text


# ---------- 여러 줄 비고 붙여넣기 ----------


def test_excel_copied_multiline_note_stays_in_one_row():
    text = (
        '1팀\t김합성\t소방경\t50000\t0101\t00000101\t"첫째 줄\r\n\r\n둘째 줄"\r\n'
        "1팀\t이합성\t소방위\t30000\t0102\t00000102\t\r\n"
    )
    rows = parse_pasted_raw(text)
    assert [row.name for row in rows] == ["김합성", "이합성"]
    assert rows[0].note == "첫째 줄\n\n둘째 줄"
    assert rows[0].source_issue == "" and rows[1].source_issue == ""
    assert "\r" not in rows[0].source_line and rows[0].source_line.endswith('둘째 줄"')


def test_headered_v2_paste_multiline_note_and_carry():
    text = (
        "순번\t계정 구분\t팀\t이름\t계급\t충전액\t개인번호\t비고\t이월 잔액\n"
        '1\t일반\t1팀\t김합성\t소방경\t100\t0101\t"메모\n\n끝"\t20\n'
    )
    rows = parse_pasted_raw(text)
    assert len(rows) == 1
    assert (rows[0].note, rows[0].carry, rows[0].source_issue) == ("메모\n\n끝", "20", "")


def test_multiline_note_paste_survives_http_review(auth_client):
    text = (
        '팀\t이름\t계급\t충전액\t개인번호\t비고\n1팀\t김합성\t소방경\t100\t0101\t"메모\r\n\r\n끝"\n'
    )
    response = auth_client.post("/monthly/upload", data={"month": "2026-08", "pasted": text})
    assert review_fields(response)["note_0"] == "메모\n\n끝"


def test_mixed_delimiters_and_inner_quotes_keep_line_behaviour():
    rows = parse_pasted_raw(
        '1팀\t김"합성\t소방경\t50,000원\t0101\t00000101\n1팀,이합성,소방위,30000,0102,00000102\n'
    )
    assert [row.name for row in rows] == ['김"합성', "이합성"]
    assert [row.amount for row in rows] == ["50,000원", "30000"]


def test_unclosed_quote_falls_back_to_single_line():
    rows = parse_pasted_raw(
        '1팀\t김합성\t소방경\t100\t0101\t00000101\t"닫히지 않음\n'
        "1팀\t이합성\t소방위\t200\t0102\t00000102\n"
    )
    assert len(rows) == 2
    assert rows[1].name == "이합성" and rows[1].amount == "200"


def test_overlong_quoted_record_falls_back():
    text = '1팀\t김합성\t소방경\t100\t0101\t00000101\t"' + ("가" * 100 + "\n") * 250 + '"\n'
    rows = parse_pasted_raw(text)
    # 기존 잘못된 따옴표 처리와 같이 첫 줄만 원문으로 남겨 사람이 수정하게 한다.
    assert rows[0].source_line == text.split("\n", 1)[0]
    assert len(rows[0].source_line) < 20_000


def test_quoted_note_line_limit():
    head = '1팀\t김합성\t소방경\t100\t0101\t00000101\t"'
    fits = parse_pasted_raw(
        head + "메모\n" * 98 + '끝"\n1팀\t이합성\t소방위\t200\t0102\t00000102\n'
    )
    assert [row.name for row in fits] == ["김합성", "이합성"]
    assert fits[0].note.count("\n") == 98
    too_long = parse_pasted_raw(head + "메모\n" * 100 + '끝"\n')  # 101줄
    assert too_long[0].source_line == head + "메모"


# ---------- 기존 Excel 이관 ----------


def _sheet(path, rows, header=("팀", "이름", "계급", "금액", "개인번호", "포인트번호")):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


@pytest.mark.parametrize("teams", [("1팀", "1팀"), ("1팀", "2팀")])
def test_legacy_import_through_cli_session_keeps_all_records(client, tmp_path, teams):
    path = tmp_path / "legacy.xlsx"
    _sheet(
        path,
        [
            (teams[0], "김합성", "소방경", 50000, "101", "00000101"),
            (teams[1], "이합성", "소방위", 30000, "102", "00000102"),
        ],
    )
    with db_module.SessionLocal() as session:
        result = import_excel(session, path, "2026-07")
    assert result.records == 2
    with db_module.SessionLocal() as fresh:
        assert fresh.scalar(select(func.count(Person.id))) == 2
        assert fresh.scalar(select(func.count(BalanceRecord.id))) == 2
        assert fresh.scalar(select(func.count(MonthlySnapshot.id))) == 1


def test_legacy_header_point_number_before_amount(tmp_path):
    path = tmp_path / "legacy.xlsx"
    _sheet(
        path,
        [("1팀", "김합성", "소방경", "101", "00000101", 50000)],
        header=("팀", "이름", "계급", "개인번호", "포인트번호", "금액"),
    )
    assert read_rows(path)[0].amount == 50000


@pytest.mark.parametrize(("cell", "expected"), [("50,000원", 50000), (50000.0, 50000), (0, 0)])
def test_legacy_amount_accepts_exact_values(tmp_path, cell, expected):
    path = tmp_path / "legacy.xlsx"
    _sheet(path, [("1팀", "김합성", "소방경", cell, "101", "00000101")])
    assert read_rows(path)[0].amount == expected


@pytest.mark.parametrize("cell", ["50000.5", 50000.5, "abc", "-1", "1,00", None])
def test_legacy_amount_rejects_inexact_values(tmp_path, cell):
    path = tmp_path / "legacy.xlsx"
    _sheet(path, [("1팀", "김합성", "소방경", cell, "101", "00000101")])
    with pytest.raises(ValueError, match="2행"):
        read_rows(path)


def test_legacy_duplicate_point_number_rejected(tmp_path):
    path = tmp_path / "legacy.xlsx"
    _sheet(
        path,
        [
            ("1팀", "김합성", "소방경", 100, "101", "00000101"),
            ("1팀", "이합성", "소방위", 100, "102", "0000 0101"),
        ],
    )
    with pytest.raises(ValueError, match="중복"):
        read_rows(path)


def test_ledger_import_assigns_distinct_team_colors(client, db):
    from app.services.ledger_import import _team_map

    data = SimpleNamespace(
        people=[SimpleNamespace(team_name=name) for name in ("가팀", "나팀")],
        shared_accounts=[SimpleNamespace(team_name="다팀")],
    )
    _team_map(db, data)  # type: ignore[arg-type]
    colors = [team.color for team in db.scalars(select(Team))]
    assert len(colors) == 3 and len(set(colors)) == 3
