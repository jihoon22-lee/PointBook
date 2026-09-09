"""원문을 보존하는 검수와 확정 승인 fingerprint. 영속 초안에서도 같은 계약을 재사용한다."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import FormData

from app.config import get_settings
from app.models import BalanceRecord, LedgerState, MonthlySnapshot, Person, Team
from app.services.balance import previous_totals
from app.services.dates import current_month, validate_month
from app.services.identifiers import normalize_point_no
from app.services.parsing import MAX_REQUEST_ROWS, ROW_FIELDS, RawRequestRow
from app.services.request_profiles import prepare, reset_target
from app.services.sync import ABSENT_ACTIONS, ACTION_DEACTIVATED, RequestRow, SyncAnalysis, analyze
from app.services.validation import (
    parse_balance,
    parse_expected_count,
    parse_expected_total,
    parse_money,
)


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
    pending_links: dict[str, str] = field(default_factory=dict)
    row_states: dict[str, str] = field(default_factory=dict)
    profile_differences: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    pending_profiles: dict[str, str] = field(default_factory=dict)


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
            link_state=str(form.get(f"link_state_{i}", "")),
            profile_review=str(form.get(f"profile_review_{i}", "")),
            source_line=str(form.get(f"source_line_{i}", "")),
            source_issue="" if clear_source_issues else str(form.get(f"source_issue_{i}", "")),
        )
        supplied_id = str(form.get(f"row_id_{i}", ""))
        if supplied_id:
            row.row_id = supplied_id
        rows.append(row)
    return rows


def deactivated_from_form(form: FormData) -> dict[str, str]:
    """두 비재직 구역 모두 기존 폼·초안 키를 사용해 이전 초안을 보존한다."""
    return {
        key.removeprefix("deactivated_carry_"): str(value)
        for key, value in form.multi_items()
        if key.startswith("deactivated_carry_")
    }


def canonical_money(value: str, *, balance: bool = False, expected: bool = False) -> str:
    """승인·재전송 비교에서만 유효 금액의 표시 차이를 제거한다. 원문은 보존한다."""
    try:
        if balance:
            return str(parse_balance(value))
        if expected:
            return str(parse_expected_total(value, max_rows=MAX_REQUEST_ROWS))
        return str(parse_money(value))
    except ValueError:
        return value


def canonical_row(row: RawRequestRow, *, include_carry: bool = False) -> dict[str, str]:
    value = {k: v for k, v in asdict(row).items() if k != "source_line"}
    value["amount"] = canonical_money(row.amount)
    if include_carry:
        value["carry"] = canonical_money(row.carry, balance=True)
    else:
        value.pop("carry")
    return value


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
    # 제출 원문은 재전송 대조에도 쓰므로 보완은 복사본에만 적용한다.
    raw_rows = [replace(raw) for raw in raw_rows]
    result = Review(raw_rows=raw_rows)
    people = (
        list(db.scalars(select(Person).options(joinedload(Person.team)).order_by(Person.id)))
        if raw_rows
        else []
    )
    by_name: dict[str, list[Person]] = {}
    by_personal: dict[str, list[Person]] = {}
    by_identity: dict[tuple[str, str, str], list[Person]] = {}
    for person in people:
        by_name.setdefault(person.name.strip(), []).append(person)
        if person.personal_no:
            by_personal.setdefault(person.personal_no.strip(), []).append(person)
            by_identity.setdefault(
                (person.name.strip(), person.personal_no.strip(), person.account_type), []
            ).append(person)
    by_point = {person.point_no: person for person in people}
    for raw in raw_rows:
        identity = (raw.name.strip(), raw.personal_no.strip(), raw.account_type)
        exact = by_identity.get(identity, []) if all(identity) else []
        signature = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        automatic = re.fullmatch(r"auto:([0-9]{8}):([a-f0-9]{64})", raw.link_state)
        manual = re.fullmatch(r"manual:([0-9]{8})", raw.link_state)
        try:
            normalized_point = normalize_point_no(raw.point_no)
        except ValueError:
            normalized_point = ""
        if raw.link_state not in {"", "manual", "new"} and not automatic and not manual:
            result.errors[raw.row_id] = "인원 연결 상태를 확인하세요."
        if manual and normalized_point != manual[1]:
            raw.link_state, raw.carry = "manual", ""
        if automatic:
            if raw.point_no != automatic[1]:
                # 직접 번호를 바꾸거나 비우면 그 선택을 유지한다.
                raw.link_state, raw.carry = "manual", ""
            elif signature != automatic[2] or len(exact) != 1 or exact[0].point_no != raw.point_no:
                raw.point_no, raw.link_state, raw.carry = "", "", ""
        if (
            not raw.point_no.strip()
            and raw.link_state != "new"
            and not raw.link_state.startswith("manual")
            and not raw.source_issue
            and len(exact) == 1
        ):
            raw.point_no = exact[0].point_no
            raw.link_state = f"auto:{raw.point_no}:{signature}"
        if raw.point_no.strip() and normalized_point and raw.link_state in {"", "manual"}:
            raw.link_state = f"manual:{normalized_point}"
        try:
            point = normalize_point_no(raw.point_no)
        except ValueError:
            point = ""
        linked_person = by_point.get(point)
        if raw.link_state == "new" and linked_person:
            result.errors[raw.row_id] = "이미 등록된 포인트번호입니다. 기존 인원을 연결하세요."
        if linked_person and raw.link_state != "new":
            differences = prepare(raw, linked_person)
            result.profile_differences[raw.row_id] = differences
            if any(diff["pending"] for diff in differences.values()):
                result.pending_profiles[raw.row_id] = "기존 정보와 다른 항목의 값을 선택하세요."
            result.row_states[raw.row_id] = (
                "auto" if raw.link_state.startswith("auto:") else "manual"
            )
        else:
            if raw.profile_review:
                reset_target(raw)
                raw.carry = ""
            result.row_states[raw.row_id] = "new" if raw.link_state == "new" or point else "pending"
        matches = {p.id: p for p in by_name.get(raw.name.strip(), [])}
        matches.update({p.id: p for p in by_personal.get(raw.personal_no.strip(), [])})
        result.candidates[raw.row_id] = [
            {
                "value": f"{p.id}:{p.version}",
                "label": f"{p.name} · {p.team.name if p.team else '팀 없음'} · {p.personal_no or '-'} · {p.point_no} · {'재직' if p.status == 'active' else '비재직'} · {'공용' if p.account_type == 'shared' else '일반'}",
            }
            for p in matches.values()
        ]
        if not raw.point_no.strip() and not raw.source_issue:
            result.pending_links[raw.row_id] = (
                "동일 이름·개인번호: 인원 선택"
                if len(exact) > 1
                else "포인트번호 입력 필요"
                if raw.link_state == "new"
                else "기존 인원 연결 또는 신규 선택"
            )
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
                raise ValueError(result.pending_links[raw.row_id])
            row = raw.validated()
            # prepare가 검증한 비교 결과에서만 빈 값의 명시 선택을 파생한다.
            differences = result.profile_differences.get(raw.row_id, {})
            for field_name in ("team", "grade"):
                difference = differences.get(field_name, {})
                setattr(
                    row,
                    f"clear_{field_name}",
                    difference.get("choice") == "incoming" and difference.get("incoming") == "",
                )
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
        "rows": [canonical_row(r) for r in raw_rows],
        "expected_count": expected_count,
        "expected_amount": canonical_money(expected_amount, expected=True),
        "database": database_fingerprint(db),
    }
    result.digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if not result.errors and not result.pending_profiles:
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
        if change.action in ABSENT_ACTIONS:
            try:
                values[change.point_no] = parse_balance(
                    deactivated.get(change.point_no, ""), label="이월 잔액"
                )
            except ValueError as exc:
                errors[change.point_no] = f"{change.name}: {exc}"
    return values, errors
