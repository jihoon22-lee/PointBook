"""원문을 보존하는 검수와 확정 승인 fingerprint. 영속 초안에서도 같은 계약을 재사용한다."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import FormData

from app.config import get_settings
from app.models import BalanceRecord, LedgerState, MonthlySnapshot, Person, Team
from app.services.balance import previous_totals
from app.services.dates import current_month, validate_month
from app.services.parsing import MAX_REQUEST_ROWS, ROW_FIELDS, RawRequestRow
from app.services.sync import ACTION_DEACTIVATED, RequestRow, SyncAnalysis, analyze
from app.services.validation import parse_balance, parse_expected_count, parse_expected_total


@dataclass
class Review:
    raw_rows: list[RawRequestRow]
    rows: list[RequestRow] = field(default_factory=list)
    analysis: SyncAnalysis = field(default_factory=lambda: SyncAnalysis(changes=[]))
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    prev_totals: dict[str, int] = field(default_factory=dict)
    token: str = ""
    digest: str = ""
    request_amount: int = 0
    previous_count: int | None = None
    previous_amount: int | None = None
    candidates: dict[str, list[dict[str, str]]] = field(default_factory=dict)


def raw_rows_from_form(form: FormData, *, clear_source_issues: bool = False) -> list[RawRequestRow]:
    indices = sorted(
        {
            int(match.group(1))
            for key in form
            if (
                match := re.fullmatch(
                    r"(?:point_no|personal_no|name|team|grade|amount|note|account_type|carry|row_id)_([0-9]+)",
                    key,
                )
            )
        }
    )
    if len(indices) > MAX_REQUEST_ROWS:
        raise ValueError(f"요청서는 최대 {MAX_REQUEST_ROWS}행까지 처리할 수 있습니다.")
    rows = []
    for i in indices:
        values = {key: str(form.get(f"{key}_{i}", "")) for key in ROW_FIELDS}
        values["account_type"] = str(form.get(f"account_type_{i}", ""))
        row = RawRequestRow(
            **values,
            carry=str(form.get(f"carry_{i}", "")),
            source_line=str(form.get(f"source_line_{i}", "")),
            source_issue="" if clear_source_issues else str(form.get(f"source_issue_{i}", "")),
        )
        supplied_id = str(form.get(f"row_id_{i}", ""))
        if supplied_id:
            row.row_id = supplied_id
        rows.append(row)
    return rows


def deactivated_from_form(form: FormData) -> dict[str, str]:
    return {
        key.removeprefix("deactivated_carry_"): str(value)
        for key, value in form.multi_items()
        if key.startswith("deactivated_carry_")
    }


def database_fingerprint(db: Session) -> str:
    """분석에 영향을 주는 전체 프로필·기록 상태. 공개 토큰에는 이 해시만 담는다."""
    people = db.execute(
        select(
            Person.id,
            Person.version,
            Person.point_no,
            Person.personal_no,
            Person.name,
            Person.grade,
            Person.status,
            Person.account_type,
            Person.team_id,
            Person.current_carry_balance,
            Person.current_amount,
        ).order_by(Person.id)
    ).all()
    teams = db.execute(select(Team.id, Team.name, Team.color).order_by(Team.id)).all()
    balances = db.execute(
        select(
            BalanceRecord.id,
            BalanceRecord.version,
            BalanceRecord.person_id,
            BalanceRecord.snapshot_id,
            BalanceRecord.carry_balance,
            BalanceRecord.amount,
            BalanceRecord.usage,
            BalanceRecord.total,
        ).order_by(BalanceRecord.id)
    ).all()
    months = db.execute(
        select(
            MonthlySnapshot.id,
            MonthlySnapshot.month,
            MonthlySnapshot.status,
            MonthlySnapshot.version,
        ).order_by(MonthlySnapshot.id)
    ).all()
    version = db.scalar(select(LedgerState.version).where(LedgerState.id == 1))
    value = [version, *[[list(r) for r in group] for group in (people, teams, balances, months)]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="pointbook-monthly-review-v1")


def matches_token(token: str, digest: str) -> bool:
    try:
        return bool(_serializer().loads(token, max_age=7200) == digest)
    except BadSignature:
        return False


def new_month_error(db: Session, month: str) -> str | None:
    try:
        validate_month(month)
    except ValueError as exc:
        return str(exc)
    latest = db.scalar(select(func.max(MonthlySnapshot.month)))
    if latest and month <= latest:
        if db.scalar(select(MonthlySnapshot.id).where(MonthlySnapshot.month == month)):
            return f"{month} 월은 이미 처리되었습니다. 정정 경로를 이용해 주세요."
        return (
            "최신 확정 월 이전에는 일반 확정을 할 수 없습니다. 과거 삽입은 정정 경로를 이용하세요."
        )
    return None


def review_rows(
    db: Session,
    month: str,
    raw_rows: list[RawRequestRow],
    expected_count: str = "",
    expected_amount: str = "",
) -> Review:
    result = Review(raw_rows=raw_rows)
    # 이름·개인번호는 후보 검색에만 쓴다. 관리자가 연결하기 전에는 식별하지 않는다.
    unresolved = [raw for raw in raw_rows if not raw.point_no.strip()]
    if unresolved:
        people = list(
            db.scalars(select(Person).options(joinedload(Person.team)).order_by(Person.id))
        )
        by_name: dict[str, list[Person]] = {}
        by_personal: dict[str, list[Person]] = {}
        for person in people:
            by_name.setdefault(person.name, []).append(person)
            if person.personal_no:
                by_personal.setdefault(person.personal_no, []).append(person)
        for raw in unresolved:
            matches = {p.id: p for p in by_name.get(raw.name.strip(), [])}
            matches.update({p.id: p for p in by_personal.get(raw.personal_no.strip(), [])})
            result.candidates[raw.row_id] = [
                {
                    "value": f"{p.id}:{p.version}",
                    "label": f"{p.name} · {p.team.name if p.team else '팀 없음'} · 개인번호 {p.personal_no or '-'} · {p.point_no} · {'재직' if p.status == 'active' else '비재직'} · {'공용' if p.account_type == 'shared' else '일반'}",
                }
                for p in matches.values()
            ]
    month_error = new_month_error(db, month)
    if month_error:
        result.errors["month"] = month_error
    if not raw_rows:
        result.errors["rows"] = "인원 행이 없습니다."
    ids: set[str] = set()
    points: dict[str, str] = {}
    for index, raw in enumerate(raw_rows, 1):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", raw.row_id) or raw.row_id in ids:
            result.errors[f"row-{index}"] = f"{index}행: 행 ID가 잘못되었거나 중복되었습니다."
        ids.add(raw.row_id)
        try:
            if not raw.point_no.strip() and not raw.source_issue:
                raise ValueError(
                    "기존 인원을 연결하거나, 신규 인원은 외부 포인트 시스템에서 발급받은 번호를 입력하세요. 발급 전에는 초안으로 보관할 수 있습니다."
                )
            row = raw.validated()
            if row.point_no in points:
                raise ValueError("중복된 포인트번호입니다.")
            points[row.point_no] = raw.row_id
            result.rows.append(row)
        except ValueError as exc:
            result.errors[raw.row_id] = f"{index}행: {exc}"
    if result.errors:
        return result
    try:
        result.analysis = analyze(db, result.rows)
    except ValueError as exc:
        result.errors["analysis"] = str(exc)
        return result
    result.request_amount = sum(row.amount for row in result.rows)
    totals = previous_totals(db, month)
    result.prev_totals = {
        c.point_no: totals[c.person_id]
        for c in result.analysis.changes
        if c.person_id is not None and c.person_id in totals
    }
    latest = db.scalar(select(func.max(MonthlySnapshot.month)))
    if latest:
        prior = db.execute(
            select(func.count(BalanceRecord.id), func.sum(BalanceRecord.amount))
            .join(MonthlySnapshot)
            .where(MonthlySnapshot.month == latest)
        ).one()
        result.previous_count, result.previous_amount = prior[0], prior[1] or 0
        y, m = (int(part) for part in latest.split("-"))
        following = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"
        if month != following:
            result.warnings.append("기록 없는 달이 있습니다. 실제 요청서의 처리 월을 확인하세요.")
        if (
            result.previous_amount
            and abs(result.request_amount - result.previous_amount) * 2 >= result.previous_amount
        ):
            result.warnings.append("최근 확정 월 대비 충전 총액이 50% 이상 달라졌습니다.")
    if month > current_month():
        result.warnings.append("미래 월을 선택했습니다. 요청서의 대상 월을 다시 확인하세요.")
    deactivated = sum(c.action == ACTION_DEACTIVATED for c in result.analysis.changes)
    if deactivated:
        result.warnings.append(
            f"요청서에서 빠진 일반 인원 {deactivated}명이 비재직 후보입니다. 인식 누락이나 부분 요청서가 아닌지 확인하세요."
        )
    for key, value, actual, label in (
        ("expected_count", expected_count, len(result.rows), "기대 인원"),
        ("expected_amount", expected_amount, result.request_amount, "기대 총액"),
    ):
        if value.strip():
            try:
                expected = (
                    parse_expected_count(value, max_rows=MAX_REQUEST_ROWS)
                    if key == "expected_count"
                    else parse_expected_total(value, max_rows=MAX_REQUEST_ROWS)
                )
                if expected != actual:
                    result.warnings.append(
                        f"{label}과 요청서 합계가 일치하지 않습니다. 원본을 대조해 주세요."
                    )
            except ValueError as exc:
                result.errors[key] = str(exc)
    payload = {
        "month": month,
        "rows": [
            {k: v for k, v in asdict(r).items() if k not in {"carry", "source_line"}}
            for r in raw_rows
        ],
        "expected_count": expected_count,
        "expected_amount": expected_amount,
        "database": database_fingerprint(db),
    }
    result.digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if not result.errors:
        result.token = _serializer().dumps(result.digest)
    return result


def carry_values(
    review: Review, deactivated: dict[str, str]
) -> tuple[dict[str, int], dict[str, str]]:
    values: dict[str, int] = {}
    errors: dict[str, str] = {}
    for raw, row in zip(review.raw_rows, review.rows, strict=True):
        try:
            values[row.point_no] = parse_balance(raw.carry, label="이월 잔액")
        except ValueError as exc:
            errors[raw.row_id] = f"{row.name}: {exc}"
    for change in review.analysis.changes:
        if change.action == ACTION_DEACTIVATED:
            try:
                values[change.point_no] = parse_balance(
                    deactivated.get(change.point_no, ""), label="이월 잔액"
                )
            except ValueError as exc:
                errors[change.point_no] = f"{change.name}: {exc}"
    return values, errors
