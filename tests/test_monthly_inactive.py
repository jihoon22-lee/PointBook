"""기존 비재직 이월의 필수 입력·초안·집계 및 금액 표시 독립 승인 계약."""

import json
from dataclasses import replace

from sqlalchemy import func, select

from app.models import BalanceRecord, LedgerOperation, MonthlyDraft, MonthlySnapshot
from app.services.parsing import RawRequestRow
from app.services.review import canonical_money, review_rows
from app.services.stats import month_summary
from tests.factories import make_person
from tests.monthly_helpers import review_fields, reviewed_confirm


def _request(month="2026-08"):
    return {
        "month": month,
        "point_no_0": "00000101",
        "personal_no_0": "101",
        "name_0": "합성재직",
        "account_type_0": "person",
        "grade_0": "",
        "team_0": "",
        "amount_0": "1000",
        "carry_0": "0",
    }


def test_existing_inactive_carry_required_without_partial_confirmation(auth_client, db):
    absent = make_person(db, "999", "합성비재직", status="inactive")
    response = auth_client.post("/monthly/review", data=_request())
    values = review_fields(response)
    values["ack_warnings"] = "yes"
    values.pop("deactivated_carry_00000999", None)
    rejected = auth_client.post("/monthly/confirm", data=values)
    assert rejected.status_code == 400
    assert "합성비재직: 이월 잔액" in rejected.text
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0
    db.refresh(absent)
    assert absent.status == "inactive"


def test_inactive_observation_preserves_negative_usage_and_excludes_transition_count(
    auth_client, db
):
    initial = _request("2026-06")
    assert reviewed_confirm(auth_client, initial, follow_redirects=False).status_code == 303
    absent = make_person(db, "999", "합성비재직", status="inactive")
    absent_id = absent.id
    june = db.scalar(select(MonthlySnapshot))
    db.add(
        BalanceRecord(
            person_id=absent.id,
            snapshot_id=june.id,
            carry_balance=100,
            amount=0,
            usage=0,
            total=100,
        )
    )
    db.commit()
    values = review_fields(auth_client.post("/monthly/review", data=_request()))
    values.update(deactivated_carry_00000999="200", ack_warnings="yes")
    response = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    august = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-08"))
    record = next(r for r in august.records if r.person_id == absent_id)
    assert (record.amount, record.carry_balance, record.total, record.usage) == (0, 200, 200, -100)
    assert db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07")) is None
    assert absent.status == "inactive" and absent.current_carry_balance == 200
    summary = month_summary(db, "2026-08")
    assert summary.workforce_count == 1 and summary.deactivated_count == 0
    operation = db.scalar(select(LedgerOperation).order_by(LedgerOperation.id.desc()))
    details = json.loads(operation.detail_json)
    assert (
        next(c for c in details["changes"] if c["point_no"] == absent.point_no)["action"]
        == "inactive_kept"
    )


def test_returned_inactive_has_only_request_row_balance(auth_client, db):
    make_person(db, "101", "합성재직", grade="", status="inactive")
    response = auth_client.post("/monthly/review", data=_request())
    values = review_fields(response)
    assert "deactivated_carry_00000101" not in values
    values["ack_warnings"] = "yes"
    assert (
        auth_client.post("/monthly/confirm", data=values, follow_redirects=False).status_code == 303
    )
    assert db.scalar(select(func.count(BalanceRecord.id))) == 1


def test_inactive_balance_draft_restore_and_stale_tab(auth_client, db):
    make_person(db, "999", "합성비재직", status="inactive")
    original = review_fields(auth_client.post("/monthly/review", data=_request()))
    saved = auth_client.post(
        "/drafts/save", data={**original, "deactivated_carry_00000999": "1,000"}
    )
    assert saved.status_code == 200
    state = saved.json()
    recovered = review_fields(auth_client.get("/drafts/" + state["draft_id"]))
    assert recovered["deactivated_carry_00000999"] == "1,000"
    assert (
        auth_client.post(
            "/drafts/save", data={**original, "deactivated_carry_00000999": "2,000"}
        ).status_code
        == 409
    )
    stale = {**original, "deactivated_carry_00000999": "2,000", "ack_warnings": "yes"}
    assert auth_client.post("/monthly/confirm", data=stale).status_code == 409
    draft = db.get(MonthlyDraft, state["draft_id"])
    assert json.loads(draft.payload_json)["deactivated"] == {"00000999": "1,000"}


def test_existing_draft_without_inactive_input_requires_it_after_reopen(auth_client, db):
    make_person(db, "999", "합성비재직", status="inactive")
    original = review_fields(auth_client.post("/monthly/review", data=_request()))
    draft = db.get(MonthlyDraft, original["draft_id"])
    assert json.loads(draft.payload_json)["deactivated"] == {}
    reopened = auth_client.get("/drafts/" + draft.id)
    values = review_fields(reopened)
    assert values["deactivated_carry_00000999"] == ""
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    values.update(deactivated_carry_00000999="0", ack_warnings="yes")
    assert (
        auth_client.post("/monthly/confirm", data=values, follow_redirects=False).status_code == 303
    )


def test_database_change_requires_review_and_keeps_inactive_input(auth_client, db):
    absent = make_person(db, "999", "합성비재직", status="inactive")
    values = review_fields(auth_client.post("/monthly/review", data=_request()))
    values.update(deactivated_carry_00000999="123", ack_warnings="yes")
    absent.version += 1
    db.commit()
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 409
    assert review_fields(response)["deactivated_carry_00000999"] == "123"
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_money_formatting_does_not_change_approval_or_replay(auth_client, db):
    make_person(db, "999", "합성비재직", status="inactive")
    values = review_fields(auth_client.post("/monthly/review", data=_request()))
    values.update(
        amount_0="1,000", carry_0="1,000", deactivated_carry_00000999="2,000", ack_warnings="yes"
    )
    first = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert first.status_code == 303
    values.update(amount_0="1000", carry_0="1000", deactivated_carry_00000999="2000")
    replay = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert replay.status_code == 303 and replay.headers["location"] == first.headers["location"]
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1
    values["deactivated_carry_00000999"] = "2001"
    assert auth_client.post("/monthly/confirm", data=values).status_code == 409


def test_approval_canonicalizes_only_valid_money(client, db):
    row = RawRequestRow(
        point_no="00000101", personal_no="101", name="합성", account_type="person", amount="1000"
    )
    plain = review_rows(db, "2026-08", [row], expected_amount="1000")
    formatted = review_rows(db, "2026-08", [replace(row, amount="1,000")], expected_amount="1,000")
    assert plain.digest == formatted.digest
    assert canonical_money("1,00") == "1,00"
    assert canonical_money("12.5") == "12.5"
    assert canonical_money("") == ""
