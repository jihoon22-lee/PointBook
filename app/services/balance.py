from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BalanceRecord, MonthlySnapshot, Person


def compute_usage(prev_total: int, carry_balance: int) -> int:
    """매달 사용한 합계 = 지난 달 기록의 총 잔액 − 이번 달 입력한 이월 잔액. 0 미만은 0."""
    return max(0, prev_total - carry_balance)


def compute_total(amount: int, carry_balance: int) -> int:
    """총 잔액 = 이번 달 들어온 금액 + 이번 달 입력한 이월 잔액."""
    return amount + carry_balance


def previous_total(db: Session, person_id: int, month: str) -> int:
    """해당 인원의 month 이전 가장 최근 월별 기록의 총 잔액. 없으면 0.

    단일 조인 쿼리로 조회해 월간 확정 시 인원×월 이중 순회(N+1)를 피한다.
    """
    total = db.scalar(
        select(BalanceRecord.total)
        .join(MonthlySnapshot, MonthlySnapshot.id == BalanceRecord.snapshot_id)
        .where(MonthlySnapshot.month < month, BalanceRecord.person_id == person_id)
        .order_by(MonthlySnapshot.month.desc())
        .limit(1)
    )
    return total if total is not None else 0


def build_balance_records(
    db: Session,
    month: str,
    carry_map: dict[int, int],
    amount_map: dict[int, int],
) -> list[BalanceRecord]:
    """이월 잔액/당월 금액 입력값으로 인원별 BalanceRecord를 계산한다. (저장은 하지 않음)"""
    records: list[BalanceRecord] = []
    for person_id in sorted(set(carry_map) | set(amount_map)):
        carry = carry_map.get(person_id, 0)
        amount = amount_map.get(person_id, 0)
        prev = previous_total(db, person_id, month)
        records.append(
            BalanceRecord(
                person_id=person_id,
                carry_balance=carry,
                amount=amount,
                usage=compute_usage(prev, carry),
                total=compute_total(amount, carry),
            )
        )
    return records


def recompute_record(record: BalanceRecord, prev_total: int) -> None:
    """개별 수정 후 잔액 기록 재계산 (사용 합계·총 잔액)."""
    record.usage = compute_usage(prev_total, record.carry_balance)
    record.total = compute_total(record.amount, record.carry_balance)


def create_monthly_snapshot(
    db: Session, month: str, records: list[BalanceRecord], *, commit: bool = True
) -> MonthlySnapshot:
    if db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month)) is not None:
        raise ValueError(f"{month} 월은 이미 처리되었습니다.")
    snapshot = MonthlySnapshot(month=month)
    db.add(snapshot)
    db.flush()
    for record in records:
        record.snapshot_id = snapshot.id
        db.add(record)
    if commit:
        db.commit()
    return snapshot


def last_record_for_person(db: Session, person: Person) -> BalanceRecord | None:
    """인원의 가장 최근 월별 잔액 기록 (비재직자 잔액 보존 확인용)."""
    return db.scalar(
        select(BalanceRecord)
        .join(MonthlySnapshot, MonthlySnapshot.id == BalanceRecord.snapshot_id)
        .where(BalanceRecord.person_id == person.id)
        .order_by(MonthlySnapshot.month.desc())
        .limit(1)
    )
