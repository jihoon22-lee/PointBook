"""이월 진행 상태와 비재직 잔액 상한: 초안 보존 및 실제 확정 경계."""

import json

import pytest
from sqlalchemy import select

from app.models import BalanceRecord, MonthlyDraft, MonthlySnapshot
from app.services.balance import create_monthly_snapshot
from app.services.carry_inputs import carry_input_state
from tests.factories import make_person
from tests.monthly_helpers import review_fields


@pytest.mark.parametrize(
    ("raw", "previous", "state"),
    [
        ("", 0, "missing"),
        ("  ", 100, "missing"),
        ("0", 0, "complete"),
        ("100", 100, "complete"),
        ("99", 100, "complete"),
        ("101", 100, "error"),
        ("1", 0, "error"),
        ("101", None, "complete"),
        (" ₩1,000원 ", 1000, "complete"),
        ("0000", 0, "complete"),
        ("-1", None, "error"),
        ("12.5", None, "error"),
        ("12,34", None, "error"),
        ("1 000", None, "error"),
        ("abc", None, "error"),
        ("１", None, "error"),
        ("1999999999998", None, "complete"),
        ("1999999999999", None, "error"),
    ],
)
def test_carry_states_preserve_zero_and_validate_full_money(raw, previous, state):
    assert carry_input_state(raw, previous_total=previous).status == state


def prepare(client, db, *, status="inactive", previous=100):
    kept = make_person(db, "1001", "기준인원")
    absent = make_person(db, "1002", "잔액확인", status=status)
    records = [BalanceRecord(person_id=kept.id, carry_balance=0, amount=100, total=100, usage=0)]
    if previous is not None:
        records.append(
            BalanceRecord(
                person_id=absent.id, carry_balance=previous, amount=0, total=previous, usage=0
            )
        )
    create_monthly_snapshot(db, "2026-05", records)
    response = client.post(
        "/monthly/review",
        data={
            "month": "2026-07",  # 빈 6월을 만들지 않고 직전 실제 기록과 비교한다.
            "point_no_0": kept.point_no,
            "personal_no_0": kept.personal_no,
            "name_0": kept.name,
            "grade_0": kept.grade,
            "team_0": "",
            "account_type_0": "person",
            "amount_0": "0",
            "carry_0": "0",
        },
    )
    assert response.status_code == 200
    values = review_fields(response)
    values["ack_warnings"] = "yes"
    return kept, absent, values, response


@pytest.mark.parametrize("status", ["active", "inactive"])
@pytest.mark.parametrize(("previous", "carry"), [(100, "101"), (0, "1")])
def test_absent_increase_rejected_without_ledger_write_and_preserved_in_draft(
    auth_client, db, status, previous, carry
):
    _, absent, values, _ = prepare(auth_client, db, status=status, previous=previous)
    field = f"deactivated_carry_{absent.point_no}"
    values[field] = carry
    saved = auth_client.post("/drafts/save", data=values)
    assert saved.status_code == 200
    values["draft_version"] = str(saved.json()["draft_version"])
    draft = db.get(MonthlyDraft, values["draft_id"])
    assert draft.review_state == "incomplete"
    assert json.loads(draft.payload_json)["deactivated"][absent.point_no] == carry
    reopened = auth_client.get(f"/drafts/{draft.id}")
    assert review_fields(reopened)[field] == carry
    assert "직전 총 잔액보다 1원 많습니다" in reopened.text
    assert 'data-state="error"' in reopened.text
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 400
    assert f'data-error-field="{field}"' in response.text
    assert db.scalar(select(MonthlySnapshot.id).where(MonthlySnapshot.month == "2026-07")) is None
    db.refresh(absent)
    assert absent.status == status
    assert db.scalar(select(MonthlySnapshot.id).where(MonthlySnapshot.month == "2026-06")) is None


@pytest.mark.parametrize("status", ["active", "inactive"])
@pytest.mark.parametrize(
    ("previous", "carry"), [(100, "100"), (100, "50"), (0, "0"), (None, "500")]
)
def test_absent_equal_lower_or_unobserved_allowed_and_replay_remains_valid(
    auth_client, db, status, previous, carry
):
    _, absent, values, _ = prepare(auth_client, db, status=status, previous=previous)
    values[f"deactivated_carry_{absent.point_no}"] = carry
    response = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert response.status_code == 303
    replay = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert replay.status_code == 303 and replay.headers["location"] == response.headers["location"]
    db.refresh(absent)
    assert absent.status == "inactive"
    record = db.scalar(
        select(BalanceRecord)
        .join(MonthlySnapshot)
        .where(MonthlySnapshot.month == "2026-07", BalanceRecord.person_id == absent.id)
    )
    assert (record.carry_balance, record.amount, record.total) == (int(carry), 0, int(carry))
    assert record.usage == (previous - int(carry) if previous is not None else 0)


def test_reference_is_not_input_and_survives_other_unlinked_row(auth_client, db):
    _, absent, values, response = prepare(auth_client, db, previous=0)
    assert 'placeholder="이월 잔액 입력"' in response.text
    assert 'placeholder="100"' not in response.text
    assert 'data-previous-total="0"' in response.text
    assert "직전 총 잔액: 0원" in response.text
    assert values[f"deactivated_carry_{absent.point_no}"] == ""
    values.update(
        carry_0="",
        name_1="미연결합성",
        personal_no_1="9999",
        account_type_1="person",
        amount_1="100",
    )
    response = auth_client.post("/monthly/review", data=values)
    assert response.status_code == 400
    assert "직전 총 잔액: 100원" in response.text
    assert 'data-previous-total="100"' in response.text
    assert "전체 처리 대상은 연결·검수 후 확인" in response.text
    assert review_fields(response)["carry_0"] == ""


@pytest.mark.parametrize(
    ("status", "kind"), [("active", "person"), ("inactive", "person"), ("active", "shared")]
)
def test_request_rows_still_allow_increase_for_kept_returned_and_shared(
    auth_client, db, status, kind
):
    person = make_person(db, "1001", "요청합성", status=status, account_type=kind)
    create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, carry_balance=100, amount=0, total=100, usage=0)],
    )
    response = auth_client.post(
        "/monthly/review",
        data={
            "month": "2026-07",
            "point_no_0": person.point_no,
            "personal_no_0": person.personal_no,
            "name_0": person.name,
            "grade_0": person.grade,
            "account_type_0": kind,
            "amount_0": "0",
            "carry_0": "200",
        },
    )
    values = review_fields(response)
    values["ack_warnings"] = "yes"
    assert (
        auth_client.post("/monthly/confirm", data=values, follow_redirects=False).status_code == 303
    )
    record = db.scalar(
        select(BalanceRecord).join(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07")
    )
    assert record.usage == -100


def test_stale_reference_cannot_confirm_after_previous_balance_changes(auth_client, db):
    _, absent, values, _ = prepare(auth_client, db)
    values[f"deactivated_carry_{absent.point_no}"] = "100"
    record = db.scalar(select(BalanceRecord).where(BalanceRecord.person_id == absent.id))
    record.carry_balance = record.total = 50
    db.commit()
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 409
    assert "다시 확정" in response.text
    assert "직전 총 잔액: 50원" in response.text
    assert "직전 총 잔액보다 50원 많습니다" in response.text
    assert db.scalar(select(MonthlySnapshot.id).where(MonthlySnapshot.month == "2026-07")) is None
