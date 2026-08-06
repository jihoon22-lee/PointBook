from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BalanceRecord, MonthlySnapshot, Person, Team


@dataclass
class MonthSummary:
    month: str
    count: int
    total_amount: int
    total_usage: int
    total_balance: int


def month_summary(db: Session, month: str) -> MonthSummary:
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month))
    if snapshot is None:
        return MonthSummary(month=month, count=0, total_amount=0, total_usage=0, total_balance=0)
    return MonthSummary(
        month=month,
        count=len(snapshot.records),
        total_amount=sum(r.amount for r in snapshot.records),
        total_usage=sum(r.usage for r in snapshot.records),
        total_balance=sum(r.total for r in snapshot.records),
    )


def trend(db: Session) -> list[MonthSummary]:
    snapshots = list(db.scalars(select(MonthlySnapshot).order_by(MonthlySnapshot.month)).all())
    return [month_summary(db, s.month) for s in snapshots]


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
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month))
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
        stat = team_map.get(person.team_id, team_map[None])
        stat.count += 1
        stat.total_amount += record.amount
        stat.total_usage += record.usage
        stat.total_balance += record.total
    return [team_map[t] for t in sorted(team_map, key=lambda k: (k is not None, k or 0))]


@dataclass
class PersonStat:
    person_id: int
    personal_no: str
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
        .where(BalanceRecord.snapshot_id == snapshot.id)
        .join(Person, Person.id == BalanceRecord.person_id)
        .order_by(Person.name)
    ).all()
    result: list[PersonStat] = []
    for record in records:
        person = record.person
        result.append(
            PersonStat(
                person_id=person.id,
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
