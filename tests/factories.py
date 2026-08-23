from sqlalchemy.orm import Session

from app.models import Person, Team


def make_team(db: Session, name: str = "1팀", color: str = "#4a7dbd") -> Team:
    team = Team(name=name, color=color)
    db.add(team)
    db.commit()
    return team


def make_person(
    db: Session,
    personal_no: str = "1001",
    name: str = "홍길동",
    grade: str = "소방위",
    team: Team | None = None,
    status: str = "active",
    point_no: str | None = None,
    account_type: str = "person",
) -> Person:
    person = Person(
        point_no=point_no or personal_no.zfill(8),
        personal_no=personal_no,
        name=name,
        grade=grade,
        team_id=team.id if team else None,
        status=status,
        account_type=account_type,
    )
    db.add(person)
    db.commit()
    return person
