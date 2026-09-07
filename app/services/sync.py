from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Person, Team
from app.services.identifiers import normalize_point_no
from app.services.validation import parse_money


@dataclass
class RequestRow:
    point_no: str
    personal_no: str
    name: str
    team: str = ""
    grade: str = ""
    amount: int = 0
    note: str = ""
    account_type: str = "person"

    def __post_init__(self) -> None:
        self.point_no = normalize_point_no(self.point_no)
        self.amount = parse_money(self.amount)
        if self.account_type not in {"person", "shared"}:
            raise ValueError("계정 유형은 person 또는 shared여야 합니다.")
        if not self.name.strip():
            raise ValueError("이름을 입력해 주세요.")
        if self.account_type == "person" and not self.personal_no.strip():
            raise ValueError("일반 인원은 개인번호를 입력해 주세요. 공용계정은 유형을 선택하세요.")


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
    account_type: str = "person"
    note: str = ""


@dataclass
class SyncAnalysis:
    changes: list[PersonChange]

    @property
    def request_count(self) -> int:
        return sum(1 for c in self.changes if c.action != ACTION_DEACTIVATED)


def analyze(db: Session, rows: list[RequestRow]) -> SyncAnalysis:
    """전체 인원과 대조한다. 읽기 쿼리 수는 인원 수에 비례하지 않는다."""
    people = list(db.scalars(select(Person).options(joinedload(Person.team))).all())
    by_point = {p.point_no: p for p in people}
    changes: list[PersonChange] = []
    seen: set[str] = set()
    for row in rows:
        if row.point_no in seen:
            raise ValueError("요청서에 중복된 포인트번호가 있습니다.")
        seen.add(row.point_no)
        person = by_point.get(row.point_no)
        if person is not None and person.account_type != row.account_type:
            raise ValueError("기존 계정 유형과 다릅니다. 유형 변경은 인원 편집에서 확인해 주세요.")
        changes.append(
            PersonChange(
                action=ACTION_NEW
                if person is None
                else (ACTION_RETURNED if person.status == "inactive" else ACTION_KEPT),
                point_no=row.point_no,
                personal_no=row.personal_no,
                name=row.name,
                team_name=row.team,
                grade=row.grade or (person.grade if person else ""),
                amount=row.amount,
                person_id=person.id if person else None,
                account_type=row.account_type,
                note=row.note,
                team_changed=bool(
                    person and row.team and (not person.team or person.team.name != row.team)
                ),
                profile_changed=bool(
                    person
                    and (
                        person.name != row.name
                        or (person.personal_no or "") != row.personal_no
                        or (row.grade and person.grade != row.grade)
                    )
                ),
            )
        )
    for person in people:
        if (
            person.status == "active"
            and person.account_type == "person"
            and person.point_no not in seen
        ):
            changes.append(
                PersonChange(
                    action=ACTION_DEACTIVATED,
                    point_no=person.point_no,
                    personal_no=person.personal_no or "",
                    name=person.name,
                    person_id=person.id,
                    team_name=person.team.name if person.team else "",
                    grade=person.grade,
                )
            )
    return SyncAnalysis(changes=changes)


def apply_analysis(db: Session, analysis: SyncAnalysis) -> None:
    """검증된 계획을 현재 쓰기 트랜잭션에 반영한다. 커밋은 호출자가 담당한다."""
    people = {p.id: p for p in db.scalars(select(Person)).all()}
    teams = {t.name: t for t in db.scalars(select(Team)).all()}
    for change in analysis.changes:
        person = people.get(change.person_id) if change.person_id is not None else None
        if change.action == ACTION_NEW:
            person = Person(
                point_no=change.point_no,
                personal_no=change.personal_no or None,
                name=change.name,
                grade=change.grade,
                status="active",
                account_type=change.account_type,
                current_amount=change.amount,
            )
            db.add(person)
        if person is None:
            raise ValueError("기준 인원이 변경되었습니다. 다시 검수해 주세요.")
        if change.action == ACTION_DEACTIVATED:
            person.status = "inactive"
            continue
        person.status = "active"
        person.name = change.name
        person.personal_no = change.personal_no or None
        person.grade = change.grade
        if change.team_name:
            team = teams.get(change.team_name)
            if team is None:
                team = Team(name=change.team_name)
                db.add(team)
                teams[change.team_name] = team
            person.team = team
