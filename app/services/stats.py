from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import BalanceRecord, MonthlySnapshot, Person, Team


@dataclass
class MonthSummary:
    month: str
    count: int
    total_amount: int
    total_usage: int
    total_balance: int


def _summarize(
    snapshot: MonthlySnapshot,
    previous_snapshot: MonthlySnapshot | None = None,
) -> MonthSummary:
    records = [r for r in snapshot.records if r.person.account_type == "person"]
    previous_amounts = (
        {
            record.person_id: record.amount
            for record in previous_snapshot.records
            if record.person.account_type == "person"
        }
        if previous_snapshot is not None
        else {}
    )
    processed_count = sum(
        record.amount != 0
        or (record.amount == 0 and previous_amounts.get(record.person_id, 0) != 0)
        for record in records
    )
    return MonthSummary(
        month=snapshot.month,
        count=processed_count,
        total_amount=sum(r.amount for r in records),
        total_usage=sum(r.usage for r in records),
        total_balance=sum(r.total for r in records),
    )


def month_summary(db: Session, month: str) -> MonthSummary:
    snapshot = db.scalar(
        select(MonthlySnapshot)
        .options(selectinload(MonthlySnapshot.records).selectinload(BalanceRecord.person))
        .where(MonthlySnapshot.month == month)
    )
    if snapshot is None:
        return MonthSummary(month=month, count=0, total_amount=0, total_usage=0, total_balance=0)
    previous_snapshot = db.scalar(
        select(MonthlySnapshot)
        .options(selectinload(MonthlySnapshot.records).selectinload(BalanceRecord.person))
        .where(MonthlySnapshot.month < month)
        .order_by(MonthlySnapshot.month.desc())
        .limit(1)
    )
    return _summarize(snapshot, previous_snapshot)


def trend(db: Session) -> list[MonthSummary]:
    snapshots = list(
        db.scalars(
            select(MonthlySnapshot)
            .options(selectinload(MonthlySnapshot.records).selectinload(BalanceRecord.person))
            .order_by(MonthlySnapshot.month)
        ).all()
    )
    return [
        _summarize(snapshot, snapshots[index - 1] if index > 0 else None)
        for index, snapshot in enumerate(snapshots)
    ]


def available_months(db: Session) -> list[str]:
    return list(
        db.scalars(select(MonthlySnapshot.month).order_by(MonthlySnapshot.month.desc())).all()
    )


@dataclass
class TeamStat:
    name: str
    color: str
    count: int
    total_amount: int
    total_usage: int
    total_balance: int


def team_summary(db: Session, month: str) -> list[TeamStat]:
    snapshot = db.scalar(
        select(MonthlySnapshot)
        .options(selectinload(MonthlySnapshot.records).selectinload(BalanceRecord.person))
        .where(MonthlySnapshot.month == month)
    )
    if snapshot is None:
        return []
    teams = list(db.scalars(select(Team).order_by(Team.name)).all())
    team_map: dict[int | None, TeamStat] = {
        t.id: TeamStat(
            name=t.name, color=t.color, count=0, total_amount=0, total_usage=0, total_balance=0
        )
        for t in teams
    }
    team_map[None] = TeamStat(
        name="팀 없음", color="#9aa3ad", count=0, total_amount=0, total_usage=0, total_balance=0
    )
    for record in snapshot.records:
        person = record.person
        if person.account_type != "person":
            continue
        stat = team_map.get(person.team_id, team_map[None])
        stat.count += 1
        stat.total_amount += record.amount
        stat.total_usage += record.usage
        stat.total_balance += record.total
    return [team_map[t] for t in sorted(team_map, key=lambda k: (k is not None, k or 0))]


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
    carry_balance: int
    amount: int
    usage: int
    total: int


def person_summary(db: Session, month: str) -> list[PersonStat]:
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month))
    if snapshot is None:
        return []
    records = db.scalars(
        select(BalanceRecord)
        .join(Person, Person.id == BalanceRecord.person_id)
        .options(selectinload(BalanceRecord.person).selectinload(Person.team))
        .where(BalanceRecord.snapshot_id == snapshot.id)
        .where(Person.account_type == "person")
        .order_by(Person.name)
    ).all()
    result: list[PersonStat] = []
    for record in records:
        person = record.person
        result.append(
            PersonStat(
                person_id=person.id,
                point_no=person.point_no,
                personal_no=person.personal_no,
                name=person.name,
                team_name=person.team.name if person.team else "",
                team_color=person.team.color if person.team else "",
                grade=person.grade,
                status=person.status,
                carry_balance=record.carry_balance,
                amount=record.amount,
                usage=record.usage,
                total=record.total,
            )
        )
    return result
