import pytest
from sqlalchemy import select

from app.models import BalanceRecord
from app.services.balance import (
    build_balance_records,
    compute_total,
    compute_usage,
    create_monthly_snapshot,
    last_record_for_person,
    previous_total,
    recompute_record,
)
from tests.factories import make_person


def test_compute_usage_normal():
    assert compute_usage(prev_total=10000, carry_balance=4000) == 6000


def test_compute_usage_clamped_at_zero():
    assert compute_usage(prev_total=3000, carry_balance=5000) == 0


def test_compute_total():
    assert compute_total(amount=50000, carry_balance=4000) == 54000


def test_previous_total_none(db):
    person = make_person(db, "1001", "홍길동")
    assert previous_total(db, person.id, "2026-07") == 0


def test_previous_total_uses_latest_month(db):
    person = make_person(db, "1001", "홍길동")
    older = create_monthly_snapshot(
        db,
        "2026-05",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=1000, usage=0, total=1000)],
    )
    latest = create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=5000, usage=0, total=5000)],
    )
    assert previous_total(db, person.id, "2026-07") == 5000
    assert previous_total(db, person.id, "2026-06") == 1000
    db.refresh(older)
    db.refresh(latest)


def test_previous_total_ignores_future_months(db):
    person = make_person(db, "1001", "홍길동")
    create_monthly_snapshot(
        db,
        "2026-08",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=1000, usage=0, total=1000)],
    )
    assert previous_total(db, person.id, "2026-07") == 0


def test_build_balance_records(db):
    person = make_person(db, "1001", "홍길동")
    create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=50000, usage=0, total=50000)],
    )
    records = build_balance_records(db, "2026-07", {person.id: 40000}, {person.id: 60000})
    assert len(records) == 1
    record = records[0]
    assert record.carry_balance == 40000
    assert record.amount == 60000
    assert record.usage == 10000
    assert record.total == 100000


def test_build_balance_records_new_person(db):
    person = make_person(db, "1001", "홍길동")
    records = build_balance_records(db, "2026-07", {person.id: 3000}, {person.id: 50000})
    assert records[0].usage == 0
    assert records[0].total == 53000


def test_recompute_record(db):
    person = make_person(db, "1001", "홍길동")
    snapshot = create_monthly_snapshot(db, "2026-07", [])
    record = BalanceRecord(
        snapshot_id=snapshot.id,
        person_id=person.id,
        carry_balance=20000,
        amount=0,
        usage=0,
        total=0,
    )
    recompute_record(record, prev_total=30000)
    assert record.usage == 10000
    assert record.total == 20000


def test_create_monthly_snapshot_duplicate_month(db):
    person = make_person(db, "1001", "홍길동")
    create_monthly_snapshot(
        db,
        "2026-07",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=0, usage=0, total=0)],
    )
    with pytest.raises(ValueError):
        create_monthly_snapshot(db, "2026-07", [])


def test_create_monthly_snapshot_stores_records(db):
    person = make_person(db, "1001", "홍길동")
    snapshot = create_monthly_snapshot(
        db,
        "2026-07",
        [
            BalanceRecord(
                person_id=person.id, carry_balance=1000, amount=2000, usage=3000, total=3000
            )
        ],
    )
    stored = db.scalar(select(BalanceRecord).where(BalanceRecord.snapshot_id == snapshot.id))
    assert stored is not None
    assert stored.total == 3000


def test_last_record_for_person_none(db):
    person = make_person(db, "1001", "홍길동")
    assert last_record_for_person(db, person) is None


def test_last_record_for_person_preserved(db):
    person = make_person(db, "1001", "홍길동")
    create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=50000, usage=0, total=50000)],
    )
    record = last_record_for_person(db, person)
    assert record is not None
    assert record.total == 50000
