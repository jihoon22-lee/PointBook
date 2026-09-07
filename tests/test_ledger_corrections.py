"""R14–R19: 정정 범위, 실제 관측, 불변 감사, 복구 및 HTTP 승인 회귀."""

import json
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    BalanceAdjustment,
    BalanceRecord,
    BalanceRevision,
    LedgerOperation,
    MonthlySnapshot,
)
from app.services.balance import build_balance_records, compute_total, create_monthly_snapshot
from app.services.history import record_data
from app.services.integrity import apply_repair, inspect_ledger, repair_plan
from app.services.ledger import (
    LedgerConflict,
    adjustment_plan,
    commit_plan,
    correction_plan,
    start_write,
)
from app.services.observations import latest_observations
from app.services.validation import MAX_MONEY, MAX_TOTAL, parse_balance
from tests.factories import make_person
from tests.monthly_helpers import review_fields
from tests.test_profiles import form_values


def setup_ledger(db):
    person = make_person(db, "8101", "합성정정")
    records = []
    for month, carry, amount in [("2026-05", 0, 100), ("2026-07", 80, 50), ("2026-09", 100, 30)]:
        created = build_balance_records(db, month, {person.id: carry}, {person.id: amount})
        create_monthly_snapshot(db, month, created)
        records.append(created[0])
    person.current_carry_balance, person.current_amount = 100, 30
    db.commit()
    return person, records


def correction(db, person, **changes):
    values = {
        "person_id": person.id,
        "month": "2026-05",
        "carry": "0",
        "amount": "150",
        "note": "수정 비고",
        "reason": "원본 대조",
        "request_key": uuid.uuid4().hex,
    }
    values.update(changes)
    return correction_plan(db, **values)


def apply(db, plan):
    start_write(db)
    operation = commit_plan(db, plan, plan.token, 1)
    db.commit()
    return operation


def test_past_correction_changes_only_target_and_next_actual_usage(client, db):
    person, records = setup_ledger(db)
    before = [record_data(record) for record in records]
    plan = correction(db, person)
    assert len(plan.changes) == 2
    operation = apply(db, plan)
    db.expire_all()
    assert (records[0].total, records[1].usage) == (150, 70)
    assert (records[1].carry_balance, records[1].amount, records[1].total) == (80, 50, 130)
    assert record_data(records[2]) == before[2]
    assert person.current_carry_balance + person.current_amount == 130
    assert inspect_ledger(db).healthy
    revisions = list(
        db.scalars(
            select(BalanceRevision)
            .where(BalanceRevision.record_id == records[0].id)
            .order_by(BalanceRevision.version)
        )
    )
    assert json.loads(revisions[0].data_json) == before[0]
    assert revisions[1].operation_id == operation.id
    assert latest_observations(db, through_month="2026-05", operation_id=0)[person.id].total == 100
    assert (
        latest_observations(db, through_month="2026-05", operation_id=operation.id)[person.id].total
        == 150
    )


def test_missing_month_insert_is_partial_and_recomputes_next_actual_record(client, db):
    person, records = setup_ledger(db)
    plan = correction(db, person, month="2026-06", carry="120", amount="20")
    assert plan.changes[0]["after"]["usage"] == -20
    apply(db, plan)
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-06"))
    assert snapshot.status == "partial"
    inserted = db.scalar(select(BalanceRecord).where(BalanceRecord.snapshot_id == snapshot.id))
    assert inserted.provenance == "reference_at_correction"
    assert inserted.observed_at is None
    assert (inserted.total, records[1].usage) == (140, 60)
    assert inspect_ledger(db).healthy


def test_latest_correction_updates_current_and_preserves_negative_usage(client, db):
    person, records = setup_ledger(db)
    apply(db, correction(db, person, month="2026-09", carry="200", amount="40"))
    assert (records[2].usage, records[2].total) == (-70, 240)
    assert (person.current_carry_balance, person.current_amount) == (200, 40)


def test_adjustment_is_separate_observation_and_blocks_propagation(client, db, monkeypatch):
    person, records = setup_ledger(db)
    original = record_data(records[2])
    plan = adjustment_plan(
        db,
        person_id=person.id,
        total="175",
        note="실제 잔액 확인",
        reason="외부 잔액 대조",
        request_key=uuid.uuid4().hex,
    )
    apply(db, plan)
    assert record_data(records[2]) == original
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 3
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 1
    assert latest_observations(db)[person.id].total == 175
    assert build_balance_records(db, "2026-10", {person.id: 200}, {person.id: 0})[0].usage == -25
    apply(db, correction(db, person, month="2026-09", carry="100", amount="100"))
    assert person.current_carry_balance + person.current_amount == 175
    assert inspect_ledger(db).healthy


def test_gap_adjustment_stays_previous_for_next_month(client, db, monkeypatch):
    person = make_person(db, "8102", "합성보정")
    create_monthly_snapshot(
        db, "2026-05", build_balance_records(db, "2026-05", {person.id: 0}, {person.id: 100})
    )
    person.current_amount = 100
    db.commit()
    monkeypatch.setattr("app.services.ledger.current_month", lambda: "2026-06")
    apply(
        db,
        adjustment_plan(
            db,
            person_id=person.id,
            total="80",
            note="",
            reason="잔액 관측",
            request_key=uuid.uuid4().hex,
        ),
    )
    records = build_balance_records(db, "2026-07", {person.id: 50}, {person.id: 10})
    create_monthly_snapshot(db, "2026-07", records)
    person.current_carry_balance, person.current_amount = 50, 10
    db.commit()
    plan = correction(db, person)
    assert len(plan.changes) == 1
    apply(db, plan)
    assert records[0].usage == 30
    assert inspect_ledger(db).healthy


def test_stale_and_tampered_plans_rollback(client, db):
    person, _ = setup_ledger(db)
    first = correction(db, person)
    second = correction(db, person, amount="180")
    apply(db, second)
    start_write(db)
    with pytest.raises(LedgerConflict):
        commit_plan(db, first, first.token, 1)
    db.rollback()
    plan = correction(db, person)
    plan.payload["amount"] = 999
    with pytest.raises(LedgerConflict):
        commit_plan(db, plan, plan.token, 1)
    db.rollback()
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1


def test_backup_failure_prevents_all_correction_writes(client, db, monkeypatch):
    person, records = setup_ledger(db)
    original = [record_data(record) for record in records]

    def fail():
        raise PermissionError("synthetic private path")

    monkeypatch.setattr("app.services.ledger.backup_database", fail)
    with pytest.raises(PermissionError):
        apply(db, correction(db, person))
    db.rollback()
    assert [record_data(record) for record in records] == original
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_inspection_is_read_only_and_repair_preserves_input_and_original(client, db):
    person, records = setup_ledger(db)
    records[1].total = 999
    records[2].usage = 777
    person.current_amount = 999
    db.commit()
    before = [record_data(record) for record in records]
    report = inspect_ledger(db)
    assert len(report.record_changes) == 2 and len(report.current_changes) == 1
    assert [record_data(record) for record in records] == before
    plan = repair_plan(db, reason="계산 필드 대조", request_key=uuid.uuid4().hex)
    start_write(db)
    url = apply_repair(db, plan, plan.token, 1)
    db.commit()
    assert url.startswith("/ledger/operations/")
    assert inspect_ledger(db).healthy
    assert (records[1].carry_balance, records[1].amount, records[1].total) == (80, 50, 130)
    assert records[2].usage == 30
    assert records[1].profile_data == records[2].profile_data
    from app.db import current_database_path

    saved = list((current_database_path().parent / "repair-preserved").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())["automatic_restore"] is False


@pytest.mark.parametrize("table", ["ledger_operations", "balance_revisions", "balance_adjustments"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_audit_tables_are_immutable(client, db, table, verb):
    person, _ = setup_ledger(db)
    apply(
        db,
        adjustment_plan(
            db,
            person_id=person.id,
            total="1",
            note="",
            reason="테스트",
            request_key=uuid.uuid4().hex,
        ),
    )
    sql = f"DELETE FROM {table}" if verb == "DELETE" else f"UPDATE {table} SET id=id"
    with pytest.raises(IntegrityError, match="immutable audit record"):
        db.execute(text(sql))
    db.rollback()


def test_http_preview_apply_replay_and_changed_payload(auth_client, db):
    person, records = setup_ledger(db)
    path = f"/ledger/correct/{person.id}"
    values = form_values(auth_client.get(path))
    values.update(
        month="2026-05", carry="0", amount="170", reason="HTTP 원본 확인", note="합성 메모"
    )
    preview = auth_client.post(path + "/preview", data=values)
    assert preview.status_code == 200 and "반영 전후" in preview.text
    values = form_values(preview)
    result = auth_client.post(path + "/apply", data=values, follow_redirects=False)
    assert result.status_code == 303
    assert "합성 메모" in auth_client.get(result.headers["location"]).text
    assert (
        auth_client.post(path + "/apply", data=values, follow_redirects=False).headers["location"]
        == result.headers["location"]
    )
    values["amount"] = "180"
    assert auth_client.post(path + "/apply", data=values).status_code == 409
    db.expire_all()
    assert records[0].total == 170
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1


def test_monthly_replay_and_revision_audit(auth_client, db):
    values = {
        "month": "2026-08",
        "point_no_0": "00008103",
        "account_type_0": "person",
        "personal_no_0": "8103",
        "name_0": "합성확정",
        "team_0": "",
        "grade_0": "",
        "amount_0": "100",
        "carry_0": "10",
        "note_0": "원본 비고",
    }
    preview = auth_client.post("/monthly/review", data=values)
    values = review_fields(preview)
    values["ack_warnings"] = "yes"
    first = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert first.status_code == 303
    second = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert second.status_code == 303 and second.headers["location"] == first.headers["location"]
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1
    record = db.scalar(select(BalanceRecord))
    revision = db.scalar(select(BalanceRevision))
    assert record.note == "원본 비고" and revision.operation_id is not None
    assert latest_observations(db, operation_id=0) == {}


def test_balance_total_can_be_carried_to_next_input():
    assert parse_balance(MAX_TOTAL) == MAX_TOTAL
    assert compute_total(0, MAX_TOTAL) == MAX_TOTAL
    assert compute_total(MAX_MONEY, MAX_MONEY) == MAX_TOTAL
    with pytest.raises(ValueError, match="총 잔액"):
        compute_total(1, MAX_TOTAL)
