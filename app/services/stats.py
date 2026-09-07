"""당시 정보·선택한 정정판으로 월간 활동과 관측 기준 잔액을 분리 집계한다."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, union
from sqlalchemy.orm import Session, joinedload

from app.models import BalanceAdjustment, LedgerOperation, MonthlySnapshot, Person, utcnow
from app.services.dates import validate_month
from app.services.history import profile_for_person
from app.services.observations import Observation, latest_observations, observation_events

PROVENANCE_LABELS = {
    "unknown": "당시 정보 미확인",
    "master_at_migration": "이관 시 현재정보 참고 · 당시 사실 미확인",
    "legacy_import": "기존 장부에서 보존 · 당시 정보 확인 범위 제한",
    "observed": "당시 관측 정보",
    "manual_correction": "명시적 보정·정정 정보",
    "reference_at_correction": "정정 당시 정보 참고 · 당시 사실 미확인",
    "unobserved": "기준시점 미확인 · 현재정보 참고",
}


@dataclass
class MonthSummary:
    month: str
    count: int
    total_amount: int
    total_usage: int
    total_balance: int
    processed_count: int = 0
    observed_count: int = 0
    unknown_count: int = 0
    known_balance_count: int = 0


@dataclass
class TeamStat:
    name: str
    color: str
    count: int
    total_amount: int
    total_usage: int
    total_balance: int
    processed_count: int = 0
    observed_count: int = 0
    unknown_count: int = 0


@dataclass
class PersonStat:
    person_id: int
    point_no: str
    personal_no: str | None
    name: str
    team_name: str
    team_color: str
    grade: str
    status: str
    carry_balance: int | None
    amount: int | None
    usage: int | None
    total: int | None
    account_type: str = "unknown"
    observation_month: str | None = None
    observed_at: datetime | None = None
    balance_kind: str = "monthly"
    provenance: str = "unknown"
    profile_label: str = "당시 정보 미확인"
    version: int | None = None
    note: str = ""
    activity_note: str = ""
    activity_version: int | None = None
    activity_profile: dict[str, Any] | None = None
    activity_provenance: str = "unknown"
    balance_profile: dict[str, Any] | None = None
    current_reference_total: int | None = None
    current_reference_name: str = ""


@dataclass
class Report:
    month: str
    scope: str
    account_type: str
    operation_id: int | None
    rows: list[PersonStat]
    summary: MonthSummary
    teams: list[TeamStat]
    unclassified_rows: list[PersonStat] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utcnow)


def _label(provenance: str) -> str:
    return PROVENANCE_LABELS.get(provenance, PROVENANCE_LABELS["unknown"])


def _text(profile: dict[str, Any], key: str, default: str = "") -> str:
    value = profile.get(key)
    return str(value) if value is not None else default


def _matches(profile: dict[str, Any], account_type: str, team_name: str | None) -> bool:
    return (account_type == "all" or profile.get("account_type") == account_type) and (
        team_name is None or _text(profile, "team_name") == team_name
    )


def _row(
    person: Person,
    observation: Observation | None,
    activity: Observation | None,
    *,
    current_reference: bool = False,
) -> PersonStat:
    base = observation or activity
    profile = base.profile if base else profile_for_person(person)
    provenance = base.provenance if base else "unobserved"
    return PersonStat(
        person_id=person.id,
        point_no=_text(profile, "point_no"),
        personal_no=profile.get("personal_no"),
        name=_text(profile, "name", f"계정 #{person.id} · 당시 이름 미확인"),
        team_name=_text(profile, "team_name"),
        team_color=_text(profile, "team_color", "#9aa3ad"),
        grade=_text(profile, "grade"),
        status=_text(profile, "status", "unknown"),
        carry_balance=activity.carry_balance if activity else None,
        amount=activity.amount if activity else None,
        usage=activity.usage if activity else None,
        total=observation.total if observation else None,
        account_type=_text(profile, "account_type", "unknown"),
        observation_month=observation.month if observation else None,
        observed_at=observation.observed_at if observation else None,
        balance_kind=observation.kind
        if observation
        else ("activity_only" if activity else "unobserved"),
        provenance=provenance,
        profile_label=_label(provenance),
        version=observation.version if observation else None,
        note=observation.note if observation else "",
        activity_note=activity.note if activity else "",
        activity_version=activity.version if activity else None,
        activity_profile=activity.profile if activity else None,
        activity_provenance=activity.provenance if activity else "unknown",
        balance_profile=observation.profile if observation else None,
        current_reference_total=person.current_carry_balance + person.current_amount
        if current_reference
        else None,
        current_reference_name=person.name if current_reference or not profile else "",
    )


def _teams(rows: list[PersonStat]) -> list[TeamStat]:
    result: dict[str, TeamStat] = {}
    members: dict[str, set[int]] = {}
    active: dict[str, set[int]] = {}
    observed: dict[str, set[int]] = {}
    for row in rows:
        for kind, profile in (("activity", row.activity_profile), ("balance", row.balance_profile)):
            if profile is None:
                continue
            name = _text(profile, "team_name", "당시 팀 미확인") or "팀 없음"
            if name not in result:
                result[name] = TeamStat(name, _text(profile, "team_color", "#9aa3ad"), 0, 0, 0, 0)
                members[name], active[name], observed[name] = set(), set(), set()
            members[name].add(row.person_id)
            if kind == "activity":
                result[name].total_amount += row.amount or 0
                result[name].total_usage += row.usage or 0
                active[name].add(row.person_id)
            else:
                result[name].total_balance += row.total or 0
                observed[name].add(row.person_id)
        if row.balance_kind == "unobserved":
            name = "기준시점 팀 미확인"
            if name not in result:
                result[name] = TeamStat(name, "#9aa3ad", 0, 0, 0, 0)
                members[name], active[name], observed[name] = set(), set(), set()
            members[name].add(row.person_id)
            result[name].unknown_count += 1
    for name, team in result.items():
        team.count = len(members[name])
        team.processed_count = len(active[name])
        team.observed_count = len(observed[name])
    return sorted(result.values(), key=lambda t: t.name)


def _assemble(
    month: str,
    scope: str,
    account_type: str,
    operation_id: int | None,
    people: dict[int, Person],
    events: list[Observation],
    balances: dict[int, Observation],
    person_id: int | None,
    team_name: str | None,
) -> Report:
    activities = {o.person_id: o for o in events if o.kind == "monthly"}
    candidates = set(people) if scope == "as_of" else set(balances) | set(activities)
    rows, unclassified = [], []
    for pid in candidates:
        if person_id is not None and pid != person_id:
            continue
        person = people[pid]
        balance, activity = balances.get(pid), activities.get(pid)
        selected_balance = (
            balance if balance and _matches(balance.profile, account_type, team_name) else None
        )
        selected_activity = (
            activity if activity and _matches(activity.profile, account_type, team_name) else None
        )
        if selected_balance or selected_activity:
            rows.append(_row(person, selected_balance, selected_activity))
        elif (
            balance is None
            and activity is None
            and _matches(profile_for_person(person), account_type, team_name)
        ):
            # This is explicitly present-day reference information, never a historic fact.
            rows.append(_row(person, None, None, current_reference=True))

        def classification_unknown(observation: Observation | None) -> bool:
            if observation is None:
                return False
            profile = observation.profile
            return (
                account_type != "all" and profile.get("account_type") not in {"person", "shared"}
            ) or (team_name is not None and profile.get("team_name") is None)

        unknown_balance = balance if classification_unknown(balance) else None
        unknown_activity = activity if classification_unknown(activity) else None
        if unknown_balance or unknown_activity:
            unclassified.append(_row(person, unknown_balance, unknown_activity))
    rows.sort(key=lambda row: (row.name, row.point_no, row.person_id))
    summary = MonthSummary(
        month=month,
        count=len(rows),
        total_amount=sum(row.amount or 0 for row in rows),
        total_usage=sum(row.usage or 0 for row in rows),
        total_balance=sum(row.total or 0 for row in rows),
        processed_count=sum(row.amount is not None for row in rows),
        observed_count=sum(row.observation_month == month for row in rows),
        unknown_count=sum(row.balance_kind == "unobserved" for row in rows),
        known_balance_count=sum(row.total is not None for row in rows),
    )
    return Report(
        month, scope, account_type, operation_id, rows, summary, _teams(rows), unclassified
    )


def report_cutoff(db: Session) -> int:
    """여러 조회에서 같은 불변 정정판을 사용하기 위한 작업 번호 상한."""
    return db.scalar(select(func.max(LedgerOperation.id))) or 0


def report(
    db: Session,
    month: str,
    scope: str = "observed",
    account_type: str = "person",
    person_id: int | None = None,
    team_name: str | None = None,
    operation_id: int | None = None,
) -> Report:
    """화면·Excel 공통 계약. 금액/usage는 선택월 실제 월간 기록, 잔액은 선택한 관측 범위다.

    같은 달 프로필이 바뀐 보정이 있으면 월간 활동과 잔액을 각 관측 당시 유형/팀에
    각각 귀속한다. 미관측 현재값은 참고 칸에만 두며 역사 잔액 합계에 넣지 않는다.
    """
    validate_month(month)
    if scope not in {"observed", "as_of"} or account_type not in {"person", "shared", "all"}:
        raise ValueError("올바른 잔액 범위와 계정 유형을 선택해 주세요.")
    operation_id = report_cutoff(db) if operation_id is None else operation_id
    people = {p.id: p for p in db.scalars(select(Person).options(joinedload(Person.team)))}
    events = observation_events(db, month=month, operation_id=operation_id)
    balances = (
        {o.person_id: o for o in events}
        if scope == "observed"
        else latest_observations(db, through_month=month, operation_id=operation_id)
    )
    return _assemble(
        month, scope, account_type, operation_id, people, events, balances, person_id, team_name
    )


def month_summary(db: Session, month: str, *, operation_id: int | None = None) -> MonthSummary:
    return report(db, month, operation_id=operation_id).summary


def team_summary(db: Session, month: str, *, operation_id: int | None = None) -> list[TeamStat]:
    return report(db, month, operation_id=operation_id).teams


def person_summary(db: Session, month: str, *, operation_id: int | None = None) -> list[PersonStat]:
    return report(db, month, operation_id=operation_id).rows


def available_months(db: Session) -> list[str]:
    return list(
        db.scalars(
            union(select(MonthlySnapshot.month), select(BalanceAdjustment.month)).order_by("month")
        ).all()
    )[::-1]


def trend(
    db: Session,
    *,
    scope: str = "observed",
    account_type: str = "person",
    operation_id: int | None = None,
    person_id: int | None = None,
    team_name: str | None = None,
) -> list[MonthSummary]:
    """기간 길이에 비례하는 추가 쿼리 없이 모든 실제 관측 달의 추이를 만든다."""
    if scope not in {"observed", "as_of"} or account_type not in {"person", "shared", "all"}:
        raise ValueError("올바른 잔액 범위와 계정 유형을 선택해 주세요.")
    operation_id = report_cutoff(db) if operation_id is None else operation_id
    people = {p.id: p for p in db.scalars(select(Person).options(joinedload(Person.team)))}
    events = observation_events(db, operation_id=operation_id)
    by_month: dict[str, list[Observation]] = {}
    for event in events:
        by_month.setdefault(event.month, []).append(event)
    balances: dict[int, Observation] = {}
    result = []
    for month, observations in by_month.items():
        if scope == "observed":
            balances = {}
        balances.update({o.person_id: o for o in observations})
        result.append(
            _assemble(
                month,
                scope,
                account_type,
                operation_id,
                people,
                observations,
                balances,
                person_id,
                team_name,
            ).summary
        )
    return result


SORT_FIELDS = {"name", "point_no", "team_name", "amount", "usage", "total"}


def sort_report(result: Report, sort_by: str = "name", direction: str = "asc") -> Report:
    if sort_by not in SORT_FIELDS or direction not in {"asc", "desc"}:
        raise ValueError("지원하는 정렬 기준과 방향을 선택해 주세요.")
    known = [row for row in result.rows if getattr(row, sort_by) is not None]
    missing = [row for row in result.rows if getattr(row, sort_by) is None]
    known.sort(
        key=lambda row: (getattr(row, sort_by), row.point_no, row.person_id),
        reverse=direction == "desc",
    )
    result.rows = known + missing
    return result
