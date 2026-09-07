"""정정·현재 보정·프로필 감사의 공통 승인 및 쓰기 계약."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import (
    BalanceAdjustment,
    BalanceRecord,
    BalanceRevision,
    LedgerOperation,
    LedgerState,
    MonthlySnapshot,
    Person,
    utcnow,
)
from app.services.backup import backup_database
from app.services.balance import compute_total
from app.services.dates import current_month, validate_month
from app.services.history import monthly_profile, preserve_revision, profile_for_person, record_data
from app.services.observations import latest_observations
from app.services.validation import parse_balance, parse_money


class LedgerConflict(ValueError):
    pass


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_request_key(key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", key):
        raise ValueError("작업 식별자가 잘못되었습니다. 화면을 다시 열어 주세요.")
    return key


def ledger_version(db: Session) -> int:
    version = db.scalar(select(LedgerState.version).where(LedgerState.id == 1))
    if version is None:
        raise ValueError("장부 버전 정보를 확인할 수 없습니다.")
    return version


def find_replay(
    db: Session, request_key: str, kind: str, payload: dict[str, Any]
) -> LedgerOperation | None:
    validate_request_key(request_key)
    operation = db.scalar(select(LedgerOperation).where(LedgerOperation.request_key == request_key))
    if operation and (operation.kind != kind or operation.payload_hash != payload_hash(payload)):
        raise LedgerConflict(
            "같은 작업 식별자에 다른 입력이 제출되었습니다. 새 작업으로 검토하세요."
        )
    return operation


def add_operation(
    db: Session,
    *,
    request_key: str,
    kind: str,
    payload: dict[str, Any],
    actor_id: int | None,
    reason: str,
    details: dict[str, Any],
    result_url: str,
) -> LedgerOperation:
    validate_request_key(request_key)
    operation = LedgerOperation(
        request_key=request_key,
        payload_hash=payload_hash(payload),
        kind=kind,
        actor_id=actor_id,
        reason=reason,
        detail_json=json.dumps(details, ensure_ascii=False, sort_keys=True),
        result_url=result_url,
    )
    db.add(operation)
    db.execute(
        update(LedgerState).where(LedgerState.id == 1).values(version=LedgerState.version + 1)
    )
    db.flush()
    return operation


@dataclass
class LedgerPlan:
    kind: str
    request_key: str
    payload: dict[str, Any]
    base_version: int
    changes: list[dict[str, Any]] = field(default_factory=list)
    current_before: dict[str, int] = field(default_factory=dict)
    current_after: dict[str, int] = field(default_factory=dict)
    current_changes: list[dict[str, Any]] = field(default_factory=list)
    token: str = ""

    def digest(self) -> str:
        return payload_hash({key: value for key, value in asdict(self).items() if key != "token"})


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="pointbook-ledger-plan-v1")


def sign_plan(plan: LedgerPlan) -> LedgerPlan:
    plan.token = _signer().dumps(plan.digest())
    return plan


def matches_plan(token: str, plan: LedgerPlan) -> bool:
    try:
        return bool(_signer().loads(token, max_age=7200) == plan.digest())
    except BadSignature:
        return False


def _person(db: Session, person_id: int) -> Person:
    person = db.scalar(
        select(Person).options(joinedload(Person.team)).where(Person.id == person_id)
    )
    if person is None:
        raise ValueError("대상 인원을 찾을 수 없습니다.")
    return person


def _reason(value: str) -> str:
    reason = value.strip()
    if not reason or len(reason) > 1000:
        raise ValueError("변경 사유를 1~1000자로 입력해 주세요.")
    return reason


def _note(value: str) -> str:
    if len(value) > 1000:
        raise ValueError("비고는 1000자 이하여야 합니다.")
    return value


def _current(person: Person) -> dict[str, int]:
    return {
        "carry_balance": person.current_carry_balance,
        "amount": person.current_amount,
        "total": person.current_carry_balance + person.current_amount,
        "version": person.version,
    }


def _ensure_original(db: Session, record: BalanceRecord) -> None:
    if (
        db.scalar(
            select(BalanceRevision.id).where(
                BalanceRevision.record_id == record.id, BalanceRevision.version == record.version
            )
        )
        is None
    ):
        preserve_revision(db, record, None, source="captured_before_change")
        db.flush()


def _record_at(db: Session, person_id: int, month: str) -> BalanceRecord | None:
    return db.scalar(
        select(BalanceRecord)
        .join(MonthlySnapshot)
        .options(joinedload(BalanceRecord.snapshot))
        .where(BalanceRecord.person_id == person_id, MonthlySnapshot.month == month)
    )


def correction_plan(
    db: Session,
    *,
    person_id: int,
    month: str,
    carry: str,
    amount: str,
    note: str,
    reason: str,
    request_key: str,
) -> LedgerPlan:
    validate_request_key(request_key)
    month = validate_month(month)
    payload: dict[str, Any] = {
        "person_id": person_id,
        "month": month,
        "carry_balance": parse_balance(carry),
        "amount": parse_money(amount),
        "note": _note(note),
        "reason": _reason(reason),
    }
    person = _person(db, person_id)
    record = _record_at(db, person_id, month)
    if record is None:
        latest = db.scalar(select(func.max(MonthlySnapshot.month)))
        if latest is None or month > latest:
            raise ValueError(
                "과거 삽입은 기존 장부의 마지막 월 이하에서만 가능합니다. 현재 잔액은 현재 보정을 이용하세요."
            )
    previous = latest_observations(
        db,
        before_month=month,
        before_at=record.observed_at if record else None,
        person_ids=[person_id],
    ).get(person_id)
    previous_total = previous.total if previous else None
    before = record_data(record) if record else None
    after = {
        **(before or {}),
        "month": month,
        "carry_balance": payload["carry_balance"],
        "amount": payload["amount"],
        "usage": previous_total - payload["carry_balance"] if previous_total is not None else 0,
        "total": compute_total(payload["amount"], payload["carry_balance"]),
        "note": payload["note"],
        "version": record.version + 1 if record else 1,
    }
    if not record:
        after.update(
            profile=profile_for_person(person),
            provenance="reference_at_correction",
            observed_at=None,
        )
    after["profile"] = monthly_profile(after["profile"], after["amount"], after["provenance"])
    plan = LedgerPlan(
        kind="correction",
        request_key=request_key,
        payload=payload,
        base_version=ledger_version(db),
        current_before=_current(person),
        current_after=_current(person),
    )
    plan.changes.append(
        {
            "record_id": record.id if record else None,
            "person_id": person_id,
            "before": before,
            "after": after,
        }
    )
    following = db.scalar(
        select(BalanceRecord)
        .join(MonthlySnapshot)
        .options(joinedload(BalanceRecord.snapshot))
        .where(BalanceRecord.person_id == person_id, MonthlySnapshot.month > month)
        .order_by(MonthlySnapshot.month)
        .limit(1)
    )
    if following:
        # 다음 월간 기록 전에 명시적 보정 관측이 있으면 그 관측의 total을 존중한다.
        preceding = latest_observations(
            db,
            before_month=following.snapshot.month,
            before_at=following.observed_at,
            person_ids=[person_id],
        ).get(person_id)
        next_total = (
            preceding.total
            if preceding and preceding.kind == "adjustment" and preceding.month >= month
            else after["total"]
        )
        next_usage = next_total - following.carry_balance
        if next_usage != following.usage:
            following_before = record_data(following)
            following_after = {
                **following_before,
                "usage": next_usage,
                "version": following.version + 1,
            }
            plan.changes.append(
                {
                    "record_id": following.id,
                    "person_id": person_id,
                    "before": following_before,
                    "after": following_after,
                }
            )
    latest_observed = latest_observations(db, person_ids=[person_id]).get(person_id)
    plan.current_after["version"] = person.version + 1
    if (
        latest_observed is None
        or (record is not None and latest_observed.record_id == record.id)
        or month > latest_observed.month
    ):
        plan.current_after = {
            "carry_balance": after["carry_balance"],
            "amount": after["amount"],
            "total": after["total"],
            "version": person.version + 1,
        }
    return sign_plan(plan)


def adjustment_plan(
    db: Session,
    *,
    person_id: int,
    total: str,
    note: str,
    reason: str,
    request_key: str,
) -> LedgerPlan:
    validate_request_key(request_key)
    person = _person(db, person_id)
    month = current_month()
    latest = latest_observations(db, person_ids=[person_id]).get(person_id)
    if latest and latest.month > month:
        raise ValueError(
            "미래 월 기록이 있어 현재 잔액을 보정할 수 없습니다. 대상 월을 먼저 정정하세요."
        )
    payload: dict[str, Any] = {
        "person_id": person_id,
        "month": month,
        "total": parse_balance(total, label="현재 확인 잔액"),
        "note": _note(note),
        "reason": _reason(reason),
    }
    return sign_plan(
        LedgerPlan(
            kind="adjustment",
            request_key=request_key,
            payload=payload,
            base_version=ledger_version(db),
            changes=[
                {
                    "before": _current(person),
                    "after": {
                        "month": month,
                        "total": payload["total"],
                        "profile": profile_for_person(person),
                        "note": payload["note"],
                    },
                }
            ],
            current_before=_current(person),
            current_after={
                "carry_balance": payload["total"],
                "amount": 0,
                "total": payload["total"],
                "version": person.version + 1,
            },
        )
    )


def apply_correction(db: Session, plan: LedgerPlan, actor_id: int) -> LedgerOperation:
    operation = add_operation(
        db,
        request_key=plan.request_key,
        kind=plan.kind,
        payload=plan.payload,
        actor_id=actor_id,
        reason=plan.payload["reason"],
        details={
            "changes": plan.changes,
            "current_before": plan.current_before,
            "current_after": plan.current_after,
        },
        result_url=f"/ledger/operations/{plan.request_key}",
    )
    for change in plan.changes:
        after = change["after"]
        record = db.get(BalanceRecord, change["record_id"]) if change["record_id"] else None
        if record is None:
            snapshot = db.scalar(
                select(MonthlySnapshot).where(MonthlySnapshot.month == after["month"])
            )
            if snapshot is None:
                snapshot = MonthlySnapshot(month=after["month"], status="partial")
                db.add(snapshot)
                db.flush()
            record = BalanceRecord(person_id=change["person_id"], snapshot=snapshot)
            db.add(record)
        else:
            _ensure_original(db, record)
        for key in ("carry_balance", "amount", "usage", "total", "note", "version", "provenance"):
            setattr(record, key, after[key])
        record.profile_data = json.dumps(after["profile"], ensure_ascii=False, sort_keys=True)
        record.observed_at = (
            datetime.fromisoformat(after["observed_at"]) if after["observed_at"] else None
        )
        record.snapshot.version = (record.snapshot.version or 1) + 1
        preserve_revision(db, record, operation.id, source="correction")
    person = _person(db, plan.payload["person_id"])
    person.current_carry_balance = plan.current_after["carry_balance"]
    person.current_amount = plan.current_after["amount"]
    person.version += 1
    return operation


def apply_adjustment(db: Session, plan: LedgerPlan, actor_id: int) -> LedgerOperation:
    person = _person(db, plan.payload["person_id"])
    operation = add_operation(
        db,
        request_key=plan.request_key,
        kind=plan.kind,
        payload=plan.payload,
        actor_id=actor_id,
        reason=plan.payload["reason"],
        details={
            "before": plan.current_before,
            "after": plan.current_after,
            "delta": plan.current_after["total"] - plan.current_before["total"],
        },
        result_url=f"/ledger/operations/{plan.request_key}",
    )
    db.add(
        BalanceAdjustment(
            person_id=person.id,
            month=plan.payload["month"],
            total=plan.payload["total"],
            profile_data=json.dumps(profile_for_person(person), ensure_ascii=False, sort_keys=True),
            note=plan.payload["note"],
            operation_id=operation.id,
            observed_at=utcnow(),
        )
    )
    person.current_carry_balance = plan.payload["total"]
    person.current_amount = 0
    person.version += 1
    return operation


def commit_plan(db: Session, plan: LedgerPlan, token: str, actor_id: int) -> LedgerOperation:
    """호출자가 같은 쓰기 잠금 안에서 다시 계산한 계획만 적용한다."""
    if not matches_plan(token, plan):
        raise LedgerConflict("입력·기준이 변경되었습니다. 전후 차이를 다시 검토하세요.")
    if ledger_version(db) != plan.base_version:
        raise LedgerConflict("다른 작업으로 장부 버전이 변경되었습니다. 다시 검토하세요.")
    backup_database()
    return (
        apply_correction(db, plan, actor_id)
        if plan.kind == "correction"
        else apply_adjustment(db, plan, actor_id)
    )


def start_write(db: Session) -> None:
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
