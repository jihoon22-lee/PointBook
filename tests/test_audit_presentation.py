"""변경 이력의 UTC 저장 보존과 한국 시간·금액 표시 회귀."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select

from app import db as db_module
from app.models import BalanceRecord, BalanceRevision, LedgerOperation, Person
from app.services.balance import create_monthly_snapshot
from app.services.dates import KST
from app.services.identifiers import format_point_no
from app.template_utils import kst_datetime
from tests.factories import make_person
from tests.monthly_helpers import reviewed_confirm
from tests.test_profiles import _apply, _edit_form, _new_form, form_values


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 8, 31, 20, 4, 29, tzinfo=UTC).replace(tzinfo=None), "2026-09-01 05:04:29"),
        (datetime(2026, 8, 31, 20, 4, 29, tzinfo=UTC), "2026-09-01 05:04:29"),
        (datetime(2026, 9, 1, 5, 4, 29, tzinfo=KST), "2026-09-01 05:04:29"),
        (None, "미확인"),
    ],
)
def test_korean_time_handles_utc_storage_and_already_aware_values(instant, expected):
    assert kst_datetime(instant) == expected


def add_operation(db, kind, details):
    operation = LedgerOperation(
        request_key=uuid.uuid4().hex,
        payload_hash="a" * 64,
        kind=kind,
        actor_id=1,
        reason="합성 장부 대조",
        detail_json=json.dumps(details, ensure_ascii=False),
        result_url="/ledger",
        created_at=datetime(2026, 8, 31, 20, 4, 29, tzinfo=UTC).replace(tzinfo=None),
    )
    db.add(operation)
    db.commit()
    return operation


def test_list_and_detail_show_same_korean_time_without_rewriting_history(auth_client, db):
    operation = add_operation(db, "monthly", {"month": "2026-09", "changes": []})
    before = (operation.created_at, operation.detail_json)
    listing = auth_client.get("/ledger")
    detail = auth_client.get("/ledger/operations/" + operation.request_key)
    for response in (listing, detail):
        assert response.status_code == 200
        assert "2026-09-01 05:04:29" in response.text
        assert "한국 시간" in response.text
        assert "2026-08-31 20:04:29" not in response.text
    db.refresh(operation)
    assert (operation.created_at, operation.detail_json) == before


def test_audit_record_amounts_are_formatted_but_notes_and_identifiers_are_not(auth_client, db):
    before = {
        "month": "2026-08",
        "carry_balance": 1234567,
        "amount": 1000000,
        "usage": -10000,
        "total": 2234567,
        "note": "비고 1234567\n00000111",
    }
    after = {**before, "amount": 2000000, "total": 3234567}
    operation = add_operation(db, "correction", {"changes": [{"before": before, "after": after}]})
    response = auth_client.get("/ledger/operations/" + operation.request_key)
    assert response.status_code == 200
    for amount in ("1,234,567", "1,000,000", "2,000,000", "-10,000", "2,234,567", "3,234,567"):
        assert f">{amount}<" in response.text
    assert before["note"] in response.text
    assert ">1234567<" not in response.text
    assert json.loads(operation.detail_json)["changes"][0]["before"] == before


def test_adjustment_amount_format_does_not_format_point_identifiers(auth_client, db):
    operation = add_operation(
        db,
        "adjustment",
        {
            "before": {"total": 1234567, "point_no": "00000111"},
            "after": {"total": 2234567, "point_no": "00000111"},
        },
    )
    response = auth_client.get("/ledger/operations/" + operation.request_key)
    assert response.status_code == 200
    assert ">1,234,567<" in response.text and ">2,234,567<" in response.text
    assert ">00000111<" in response.text


def _confirm_monthly_person(auth_client, db, *, account_type="person"):
    response = reviewed_confirm(
        auth_client,
        {
            "month": "2026-01",
            "point_no_0": "00003101",
            "personal_no_0": "3101" if account_type == "person" else "",
            "account_type_0": account_type,
            "name_0": "합성원본계정",
            "team_0": "",
            "grade_0": "",
            "amount_0": "100",
            "carry_0": "0",
            "note_0": "당시 비고\n\n보존",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    person = db.scalar(select(Person).where(Person.point_no == "00003101"))
    operation = db.scalar(select(LedgerOperation).where(LedgerOperation.kind == "monthly"))
    return person, operation


def _edit_person(auth_client, db, person, **changes):
    path = f"/people/{person.id}/edit"
    values = _edit_form(auth_client, person, **changes)
    preview = auth_client.post(path, data=values)
    assert preview.status_code == 200
    applied = _apply(auth_client, path, values, preview, confirm_identity="yes")
    assert applied.status_code == 303
    db.expire_all()


@pytest.mark.parametrize("account_type", ["person", "shared"])
def test_monthly_audit_retains_original_person_after_point_number_reuse(
    auth_client, db, account_type
):
    original, monthly = _confirm_monthly_person(auth_client, db, account_type=account_type)
    original_details = monthly.detail_json
    original_revision = db.scalar(select(BalanceRevision.data_json))
    detail_url = "/ledger/operations/" + monthly.request_key
    _edit_person(auth_client, db, original, point_no="90003101", name="합성현재이름")

    renamed = auth_client.get(detail_url)
    assert renamed.status_code == 200

    values = _new_form(auth_client, point_no="00003101", personal_no="3102", name="합성별도계정")
    preview = auth_client.post("/people/new", data=values)
    assert preview.status_code == 200
    assert _apply(auth_client, "/people/new", values, preview).status_code == 303
    db.expire_all()

    page = auth_client.get(detail_url)
    assert page.status_code == 200
    assert page.context["monthly_summary"] == [{"label": "신규", "names": ["합성현재이름"]}]
    assert renamed.context["monthly_summary"] == [{"label": "신규", "names": ["합성현재이름"]}]
    assert "합성현재이름" in page.text
    assert "합성별도계정" not in page.text and "합성원본계정" not in page.text
    report = auth_client.get("/dashboard?month=2026-01&account_type=all")
    assert "합성현재이름" in report.text and "합성별도계정" not in report.text
    db.refresh(monthly)
    assert monthly.detail_json == original_details
    assert db.scalar(select(BalanceRevision.data_json)) == original_revision


def _correct_month(auth_client, person, *, month="2026-01"):
    path = f"/ledger/correct/{person.id}"
    values = {
        **form_values(auth_client.get(f"{path}?month={month}")),
        "carry": "0",
        "amount": "150",
        "note": "합성 금액 정정",
        "reason": "원본 금액 대조",
    }
    preview = auth_client.post(path + "/preview", data=values)
    assert preview.status_code == 200
    response = auth_client.post(path + "/apply", data=form_values(preview), follow_redirects=False)
    assert response.status_code == 303


def test_monthly_audit_uses_first_revision_after_name_and_amount_changes(auth_client, db):
    original, monthly = _confirm_monthly_person(auth_client, db)
    first_revision = db.scalar(select(BalanceRevision))
    original_data = first_revision.data_json
    original_details = monthly.detail_json
    _edit_person(auth_client, db, original, name="합성수정이름")
    _correct_month(auth_client, original)
    db.expire_all()
    revisions = list(db.scalars(select(BalanceRevision).order_by(BalanceRevision.version)))
    assert len(revisions) == 2
    assert revisions[0].operation_id == monthly.id
    assert revisions[1].operation_id != monthly.id

    page = auth_client.get("/ledger/operations/" + monthly.request_key)
    assert page.status_code == 200
    assert page.context["monthly_summary"] == [{"label": "신규", "names": ["합성수정이름"]}]
    assert "합성수정이름" in page.text and "합성원본계정" not in page.text
    db.refresh(revisions[0])
    db.refresh(monthly)
    assert revisions[0].data_json == original_data
    assert monthly.detail_json == original_details


@pytest.mark.parametrize("insert_history", [False, True])
def test_monthly_audit_without_original_link_never_guesses_current_number_owner(
    auth_client, db, insert_history
):
    point_no = "00003101"
    monthly = add_operation(
        db,
        "monthly",
        {"month": "2026-01", "changes": [{"point_no": point_no, "action": "kept"}]},
    )
    details_before = monthly.detail_json
    create_monthly_snapshot(db, "2026-01", [])
    replacement = make_person(db, "3102", "합성별도계정", point_no=point_no)
    if insert_history:
        # 같은 월·번호의 기록이 나중에 생겨도 최초 월간 확정의 인원 근거가 아니다.
        _correct_month(auth_client, replacement)
        inserted = db.scalar(select(BalanceRevision))
        assert inserted.version == 1 and inserted.operation_id != monthly.id

    page = auth_client.get("/ledger/operations/" + monthly.request_key)
    assert page.status_code == 200
    assert page.context["monthly_summary"] == [
        {"label": "재직 유지", "names": [format_point_no(point_no)]}
    ]
    assert "합성별도계정" not in page.text
    assert format_point_no(point_no) in page.text
    db.refresh(monthly)
    assert monthly.detail_json == details_before


def test_monthly_audit_resolves_many_accounts_with_bounded_queries(auth_client, db):
    people = [
        make_person(db, str(number), f"합성인원{number}", point_no=f"{number:08d}")
        for number in range(1, 26)
    ]
    monthly = add_operation(
        db,
        "monthly",
        {
            "month": "2026-01",
            "changes": [{"point_no": person.point_no, "action": "kept"} for person in people],
        },
    )
    create_monthly_snapshot(
        db,
        "2026-01",
        [BalanceRecord(person_id=person.id, amount=100, total=100) for person in people],
        operation_id=monthly.id,
    )
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db_module.engine, "before_cursor_execute", capture)
    try:
        page = auth_client.get("/ledger/operations/" + monthly.request_key)
    finally:
        event.remove(db_module.engine, "before_cursor_execute", capture)
    assert page.status_code == 200
    assert page.context["monthly_summary"] == [
        {"label": "재직 유지", "names": [person.name for person in people]}
    ]
    assert len(statements) <= 6
