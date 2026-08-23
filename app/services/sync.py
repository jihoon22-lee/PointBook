from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Person
from app.services.identifiers import normalize_point_no
from app.services.teams import get_or_create_team


@dataclass
class RequestRow:
    point_no: str
    personal_no: str
    name: str
    team: str = ""
    grade: str = ""
    amount: int = 0
    note: str = ""

    def __post_init__(self) -> None:
        self.point_no = normalize_point_no(self.point_no)


ACTION_KEPT = "kept"
ACTION_RETURNED = "returned"
ACTION_NEW = "new"
ACTION_DEACTIVATED = "deactivated"


@dataclass
class PersonChange:
    action: str
    point_no: str
    personal_no: str
    name: str
    team_name: str = ""
    grade: str = ""
    amount: int = 0
    person_id: int | None = None
    team_changed: bool = False
    profile_changed: bool = False


@dataclass
class SyncAnalysis:
    changes: list[PersonChange]

    @property
    def request_count(self) -> int:
        return sum(1 for c in self.changes if c.action != ACTION_DEACTIVATED)


def _find_person(db: Session, row: RequestRow) -> Person | None:
    return db.scalar(select(Person).where(Person.point_no == row.point_no))


def _team_changed(person: Person, row: RequestRow) -> bool:
    if not row.team:
        return False
    return person.team is None or person.team.name != row.team


def _profile_changed(person: Person, row: RequestRow) -> bool:
    return (
        person.name != row.name
        or person.personal_no != row.personal_no
        or bool(row.grade and person.grade != row.grade)
    )


def analyze(db: Session, rows: list[RequestRow]) -> SyncAnalysis:
    """요청서 리스트를 DB 전체 인원과 대조해 변경 계획을 계산한다. DB를 변경하지 않는다."""
    changes: list[PersonChange] = []
    seen_ids: set[int] = set()
    seen_point_nos: set[str] = set()

    for row in rows:
        if row.point_no in seen_point_nos:
            raise ValueError("요청서에 중복된 포인트번호가 있습니다.")
        seen_point_nos.add(row.point_no)
        person = _find_person(db, row)
        if person is None:
            changes.append(
                PersonChange(
                    action=ACTION_NEW,
                    point_no=row.point_no,
                    personal_no=row.personal_no,
                    name=row.name,
                    team_name=row.team,
                    grade=row.grade,
                    amount=row.amount,
                )
            )
            continue
        seen_ids.add(person.id)
        action = ACTION_RETURNED if person.status == "inactive" else ACTION_KEPT
        changes.append(
            PersonChange(
                action=action,
                point_no=person.point_no,
                personal_no=row.personal_no,
                name=row.name,
                team_name=row.team,
                grade=row.grade or person.grade,
                amount=row.amount,
                person_id=person.id,
                team_changed=_team_changed(person, row),
                profile_changed=_profile_changed(person, row),
            )
        )

    active = db.scalars(
        select(Person).where(Person.status == "active", Person.account_type == "person")
    ).all()
    for person in active:
        if person.id not in seen_ids:
            changes.append(
                PersonChange(
                    action=ACTION_DEACTIVATED,
                    point_no=person.point_no,
                    personal_no=person.personal_no or "",
                    name=person.name,
                    person_id=person.id,
                )
            )
    return SyncAnalysis(changes=changes)


def apply_analysis(db: Session, analysis: SyncAnalysis) -> None:
    """분석 결과를 DB에 반영한다. (재직/비재직 전환, 팀 변경, 신규 추가)"""
    for change in analysis.changes:
        if change.action == ACTION_NEW:
            team = get_or_create_team(db, change.team_name) if change.team_name else None
            db.add(
                Person(
                    point_no=change.point_no,
                    personal_no=change.personal_no,
                    name=change.name,
                    grade=change.grade,
                    status="active",
                    account_type="person",
                    team_id=team.id if team else None,
                    current_amount=change.amount,
                )
            )
            continue
        if change.person_id is None:
            continue
        person = db.get(Person, change.person_id)
        if person is None:
            continue
        if change.action == ACTION_DEACTIVATED:
            person.status = "inactive"
            continue
        if change.action == ACTION_RETURNED:
            person.status = "active"
        person.name = change.name
        person.personal_no = change.personal_no
        if change.team_name:
            team = get_or_create_team(db, change.team_name)
            if person.team_id != team.id:
                person.team_id = team.id
        if change.grade:
            person.grade = change.grade
