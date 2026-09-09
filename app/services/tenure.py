"""월별 재직 근거와 명시적 상태 변경으로 소방서 재직 기간을 읽는다."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import BalanceRecord, LedgerOperation, Person
from app.services.dates import KST, current_month, validate_month
from app.services.history import profile_for_record


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:]) - 1


def _short_month(index: int) -> str:
    year, month = divmod(index, 12)
    return f"{year % 100:02d}/{month + 1:02d}"


def _period(start: int, end: int, *, ongoing: bool = False) -> str:
    years, months = divmod(end - start + 1, 12)
    duration = " ".join(
        part for part in (f"{years}년" if years else "", f"{months}개월" if months else "") if part
    )
    return f"{duration} ({_short_month(start)} ~ {'' if ongoing else _short_month(end)})"


def tenure_labels(
    db: Session, people: Sequence[Person], *, through_month: str | None = None
) -> dict[int, str]:
    """인원 수에 비례하는 추가 쿼리 없이 기간을 계산한다. 공백 월에 기록을 만들지 않는다.

    비재직 근거가 없으면 월 사이 공백을 연결한다. 첫 관측 이전 기간은 추정하지 않으며,
    마지막 근거와 현재 상태가 충돌하면 정확한 종료/복귀월을 알 수 없어 미확인으로 둔다.
    """
    cutoff = validate_month(through_month or current_month())
    ids = [person.id for person in people if person.account_type == "person"]
    result = {person.id: "-" if person.account_type == "shared" else "미확인" for person in people}
    if not ids:
        return result
    # 월별로 마지막 명시적 상태를 사용한다. 이관 기록은 해당 월의 지급액 계약을 따른다.
    evidence: dict[int, dict[str, tuple[datetime, int, str]]] = {pid: {} for pid in ids}

    def remember(pid: int, month: str, at: datetime | None, order: int, status: str) -> None:
        if month > cutoff or status not in {"active", "inactive"}:
            return
        value = ((at or datetime.min.replace(tzinfo=UTC)).replace(tzinfo=UTC), order, status)
        old = evidence[pid].get(month)
        if old is None or value[:2] >= old[:2]:
            evidence[pid][month] = value

    records = db.scalars(
        select(BalanceRecord)
        .where(BalanceRecord.person_id.in_(ids))
        .options(joinedload(BalanceRecord.snapshot))
    )
    monthly_people: dict[tuple[str, str], int] = {}
    for record in records:
        profile = profile_for_record(record)
        if profile.get("account_type") == "person":
            monthly_people[(record.snapshot.month, profile.get("point_no", ""))] = record.person_id
            remember(
                record.person_id,
                record.snapshot.month,
                record.observed_at,
                record.id,
                profile.get("status", ""),
            )
    operations = db.scalars(
        select(LedgerOperation).where(
            or_(
                and_(
                    LedgerOperation.kind == "profile",
                    func.json_extract(LedgerOperation.detail_json, "$.person_id").in_(ids),
                ),
                LedgerOperation.kind == "monthly",
            ),
        )
    )
    for operation in operations:
        try:
            details = json.loads(operation.detail_json)
            changes = details.get("changes", [])
            month = operation.created_at.replace(tzinfo=UTC).astimezone(KST).strftime("%Y-%m")
            if operation.kind == "monthly":
                # 과거 월 확정도 현재 상태를 바꾼다. 장부 월과 별도로 실제 변경 월을 읽는다.
                # 같은 상태 유지는 새 복귀·비재직 전환으로 세지 않는다.
                for change in changes:
                    pid = monthly_people.get((details.get("month", ""), change.get("point_no", "")))
                    status = {"new": "active", "returned": "active", "deactivated": "inactive"}.get(
                        change.get("action")
                    )
                    before = change.get("before") or {}
                    if pid is not None and status and before.get("status") != status:
                        remember(pid, month, operation.created_at, operation.id, status)
                continue
            pid = details.get("person_id")
            if pid not in evidence:
                continue
            for change in changes:
                before, after = change.get("before"), change.get("after", {})
                if after.get("account_type") != "person" or (
                    before and before.get("status") == after.get("status")
                ):
                    continue
                remember(pid, month, operation.created_at, operation.id, after.get("status", ""))
        except (TypeError, ValueError, AttributeError):
            continue
    end_now = _month_index(cutoff)
    for person in people:
        months = sorted(evidence.get(person.id, {}).items())
        if not months or months[-1][1][2] != person.status:
            continue
        periods: list[str] = []
        start: int | None = None
        for month, (_, _, status) in months:
            index = _month_index(month)
            if status == "active" and start is None:
                start = index
            elif status == "inactive" and start is not None:
                periods.append(_period(start, index - 1))
                start = None
        if start is not None:
            periods.append(_period(start, end_now, ongoing=True))
        if periods:
            result[person.id] = ", ".join(reversed(periods))
    return result
