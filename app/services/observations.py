"""월간 기록과 명시적 잔액 관측의 시간 순서·정정판을 한 번에 조회한다."""

import json
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Integer,
    Select,
    and_,
    case,
    func,
    literal,
    or_,
    select,
    type_coerce,
    union_all,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Subquery

from app.models import (
    BalanceAdjustment,
    BalanceRecord,
    BalanceRevision,
    LedgerOperation,
    MonthlySnapshot,
)
from app.services.dates import validate_month
from app.services.history import monthly_profile


@dataclass(frozen=True)
class Observation:
    person_id: int
    month: str
    total: int
    carry_balance: int | None
    amount: int | None
    usage: int | None
    profile: dict[str, Any]
    provenance: str
    kind: str
    observed_at: datetime | None
    record_id: int | None
    adjustment_id: int | None
    version: int
    note: str
    previous_monthly_status: str | None = None
    previous_monthly_type: str | None = None
    monthly_action: str | None = None


def _monthly_query(operation_id: int | None) -> Select[Any]:
    if operation_id is None:
        return select(
            BalanceRecord.person_id.label("person_id"),
            MonthlySnapshot.month.label("month"),
            BalanceRecord.total,
            BalanceRecord.carry_balance,
            BalanceRecord.amount,
            BalanceRecord.usage,
            BalanceRecord.profile_data,
            BalanceRecord.provenance,
            literal("monthly").label("kind"),
            BalanceRecord.observed_at,
            BalanceRecord.id.label("record_id"),
            literal(None, Integer).label("adjustment_id"),
            BalanceRecord.version,
            BalanceRecord.note,
            BalanceRecord.id.label("order_id"),
            literal(0).label("kind_order"),
        ).join(MonthlySnapshot, MonthlySnapshot.id == BalanceRecord.snapshot_id)
    ranked = (
        select(
            BalanceRevision.record_id,
            BalanceRevision.version,
            BalanceRevision.data_json,
            func.row_number()
            .over(partition_by=BalanceRevision.record_id, order_by=BalanceRevision.version.desc())
            .label("position"),
        )
        .where(
            or_(
                BalanceRevision.operation_id.is_(None), BalanceRevision.operation_id <= operation_id
            )
        )
        .subquery()
    )
    data = ranked.c.data_json
    raw_time = func.replace(func.json_extract(data, "$.observed_at"), "T", " ")
    fractional_time = case((func.instr(raw_time, ".") > 0, raw_time), else_=raw_time.concat("."))
    return (
        select(
            BalanceRecord.person_id.label("person_id"),
            func.json_extract(data, "$.month").label("month"),
            *[
                func.json_extract(data, f"$.{key}").label(key)
                for key in ("total", "carry_balance", "amount", "usage")
            ],
            func.json_extract(data, "$.profile").label("profile_data"),
            func.json_extract(data, "$.provenance").label("provenance"),
            literal("monthly").label("kind"),
            type_coerce(
                func.substr(fractional_time.concat("000000"), 1, 26),
                DateTime,
            ).label("observed_at"),
            BalanceRecord.id.label("record_id"),
            literal(None, Integer).label("adjustment_id"),
            ranked.c.version,
            func.json_extract(data, "$.note").label("note"),
            BalanceRecord.id.label("order_id"),
            literal(0).label("kind_order"),
        )
        .join(ranked, ranked.c.record_id == BalanceRecord.id)
        .where(ranked.c.position == 1)
    )


def _source(operation_id: int | None = None) -> Subquery:
    monthly = _monthly_query(operation_id).subquery()
    monthly_with_previous = select(
        monthly,
        *[
            func.lag(monthly.c[key])
            .over(partition_by=monthly.c.person_id, order_by=monthly.c.month)
            .label("previous_" + key)
            for key in ("profile_data", "amount", "provenance")
        ],
    )
    adjustments = select(
        BalanceAdjustment.person_id,
        BalanceAdjustment.month,
        BalanceAdjustment.total,
        literal(None, Integer).label("carry_balance"),
        literal(None, Integer).label("amount"),
        literal(None, Integer).label("usage"),
        BalanceAdjustment.profile_data,
        literal("manual_correction").label("provenance"),
        literal("adjustment").label("kind"),
        BalanceAdjustment.observed_at,
        literal(None, Integer).label("record_id"),
        BalanceAdjustment.id.label("adjustment_id"),
        literal(1).label("version"),
        BalanceAdjustment.note,
        BalanceAdjustment.id.label("order_id"),
        literal(1).label("kind_order"),
        literal(None).label("previous_profile_data"),
        literal(None).label("previous_amount"),
        literal(None).label("previous_provenance"),
    )
    if operation_id is not None:
        if operation_id < 0:
            raise ValueError("정정판 작업 번호는 0 이상이어야 합니다.")
        adjustments = adjustments.where(BalanceAdjustment.operation_id <= operation_id)
    return union_all(monthly_with_previous, adjustments).subquery()


def _read(row: Any) -> Observation:
    try:
        profile = json.loads(row.profile_data or "{}")
    except (TypeError, ValueError):
        profile = {}
    try:
        previous = json.loads(row.previous_profile_data or "{}")
    except (TypeError, ValueError):
        previous = {}
    previous = monthly_profile(
        previous if isinstance(previous, dict) else {}, row.previous_amount, row.previous_provenance
    )
    return Observation(
        person_id=row.person_id,
        month=row.month,
        total=row.total,
        carry_balance=row.carry_balance,
        amount=row.amount,
        usage=row.usage,
        profile=monthly_profile(
            profile if isinstance(profile, dict) else {}, row.amount, row.provenance
        ),
        provenance=row.provenance or "unknown",
        kind=row.kind,
        observed_at=row.observed_at,
        record_id=row.record_id,
        adjustment_id=row.adjustment_id,
        version=row.version,
        note=row.note or "",
        previous_monthly_status=previous.get("status"),
        previous_monthly_type=previous.get("account_type"),
    )


def latest_observations(
    db: Session,
    *,
    through_month: str | None = None,
    before_month: str | None = None,
    before_at: datetime | None = None,
    person_ids: Collection[int] | None = None,
    operation_id: int | None = None,
) -> dict[int, Observation]:
    """계정별 마지막 실제 관측. 과거가 미확인인 월간 시각 NULL은 같은 달에서 가장 먼저다.

    before_month는 월간 기록을 엄격히 이전 달로 제한한다. before_at이 함께 있으면
    같은 달의 더 이른 보정 관측은 직전 잔액에 사용할 수 있다. 동일 월/시각은 ID,
    ID까지 같으면 보정을 뒤로 정렬한다. 읽기로 빈 달의 관측을 생성하지 않는다.
    """
    source = _source(operation_id)
    conditions = []
    if through_month is not None:
        conditions.append(source.c.month <= validate_month(through_month))
    if before_month is not None:
        validate_month(before_month)
        prior = source.c.month < before_month
        if before_at is not None:
            prior = or_(
                prior,
                and_(
                    source.c.month == before_month,
                    source.c.kind == "adjustment",
                    source.c.observed_at < before_at,
                ),
            )
        conditions.append(prior)
    elif before_at is not None:
        conditions.append(or_(source.c.observed_at.is_(None), source.c.observed_at < before_at))
    if person_ids is not None:
        conditions.append(source.c.person_id.in_(person_ids))
    ranked = (
        select(
            source,
            func.row_number()
            .over(
                partition_by=source.c.person_id,
                order_by=(
                    source.c.month.desc(),
                    source.c.observed_at.desc(),
                    source.c.order_id.desc(),
                    source.c.kind_order.desc(),
                ),
            )
            .label("position"),
        )
        .where(*conditions)
        .subquery()
    )
    rows = db.execute(select(ranked).where(ranked.c.position == 1)).all()
    return {row.person_id: _read(row) for row in rows}


def observation_events(
    db: Session, *, month: str | None = None, operation_id: int | None = None
) -> list[Observation]:
    """보고서의 월간 활동·추이용 조회. 선택한 정정판의 최신 기록만 반환한다."""
    source = _source(operation_id)
    stmt = select(source).order_by(
        source.c.month, source.c.observed_at, source.c.order_id, source.c.kind_order
    )
    if month is not None:
        stmt = stmt.where(source.c.month == validate_month(month))
    events = [_read(row) for row in db.execute(stmt)]
    operations = select(LedgerOperation.detail_json).where(LedgerOperation.kind == "monthly")
    if operation_id is not None:
        operations = operations.where(LedgerOperation.id <= operation_id)
    if month is not None:
        operations = operations.where(
            func.json_extract(LedgerOperation.detail_json, "$.month") == month
        )
    actions = {}
    for raw in db.scalars(operations):
        details = json.loads(raw)
        for change in details["changes"]:
            actions[(details["month"], change["point_no"])] = change["action"]
    # 월간 확정이 없는 이관 자료는 _read의 기본 monthly_action=None을 그대로 쓴다.
    if not actions:
        return events
    # 확정 당시 번호로 연결한다. 이후 번호 변경·개별 복귀에도 실제 전환 사실을 유지한다.
    return [
        replace(event, monthly_action=actions.get((event.month, event.profile.get("point_no"))))
        if event.kind == "monthly" and event.provenance == "observed"
        else event
        for event in events
    ]
