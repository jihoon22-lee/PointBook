"""장부의 고정 당시 정보와 불변 revision을 생성한다."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import BalanceRecord, BalanceRevision, Person, utcnow

LEGACY_SOURCES = frozenset({"master_at_migration", "legacy_import"})


def monthly_profile(profile: dict[str, Any], amount: int | None, provenance: str) -> dict[str, Any]:
    """기존 엑셀 장부의 지급 이력으로 월별 상태를 읽는다. 원본은 변경하지 않는다."""
    result = dict(profile)
    if provenance in LEGACY_SOURCES and amount is not None:
        if result.get("account_type") == "person":
            result["status"] = "active" if amount > 0 else "inactive"
        elif result.get("account_type") == "shared":
            result["status"] = "active"
    return result


def current_person_profile(profile: dict[str, Any], person: Person) -> dict[str, Any]:
    """인원 식별·이름·소속은 인원 마스터 한 곳에서 관리한다."""
    return {
        **profile,
        "name": person.name,
        "point_no": person.point_no,
        "personal_no": person.personal_no,
        "team_id": person.team_id,
        "team_name": person.team.name if person.team else "",
        "team_color": person.team.color if person.team else "#9aa3ad",
        "grade": person.grade,
    }


def profile_for_person(person: Person) -> dict[str, Any]:
    return {
        "point_no": person.point_no,
        "personal_no": person.personal_no,
        "name": person.name,
        "grade": person.grade,
        "status": person.status,
        "account_type": person.account_type,
        "team_id": person.team_id,
        "team_name": person.team.name if person.team else "",
        "team_color": person.team.color if person.team else "#9aa3ad",
    }


def profile_for_record(record: BalanceRecord) -> dict[str, Any]:
    try:
        value = json.loads(record.profile_data)
        return (
            monthly_profile(value, record.amount, record.provenance)
            if isinstance(value, dict)
            else {}
        )
    except (TypeError, ValueError):
        return {}


def record_data(record: BalanceRecord, month: str | None = None) -> dict[str, Any]:
    return {
        "month": month or record.snapshot.month,
        "carry_balance": record.carry_balance,
        "amount": record.amount,
        "usage": record.usage,
        "total": record.total,
        "note": record.note,
        "profile": profile_for_record(record),
        "provenance": record.provenance,
        "observed_at": record.observed_at.isoformat(timespec="microseconds")
        if record.observed_at
        else None,
        "version": record.version,
    }


def preserve_revision(
    db: Session, record: BalanceRecord, operation_id: int | None, *, source: str
) -> None:
    db.flush()
    db.add(
        BalanceRevision(
            record_id=record.id,
            version=record.version,
            operation_id=operation_id,
            data_json=json.dumps(record_data(record), ensure_ascii=False, sort_keys=True),
            source=source,
        )
    )


def freeze_new_records(
    db: Session, records: list[BalanceRecord], *, source: str = "observed"
) -> None:
    people = {p.id: p for p in db.scalars(select(Person).options(joinedload(Person.team)))}
    for record in records:
        if not record.profile_data or record.profile_data == "{}":
            record.profile_data = json.dumps(
                profile_for_person(people[record.person_id]), ensure_ascii=False, sort_keys=True
            )
        record.provenance = source
        if source in LEGACY_SOURCES:
            record.profile_data = json.dumps(
                profile_for_record(record), ensure_ascii=False, sort_keys=True
            )
        record.observed_at = utcnow() if source == "observed" else None
        record.version = record.version or 1
        record.note = record.note or ""
