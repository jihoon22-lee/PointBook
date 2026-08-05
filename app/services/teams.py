from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team

TEAM_COLORS = [
    "#4a7dbd",
    "#2e9e5b",
    "#c0392b",
    "#b8860b",
    "#8e44ad",
    "#16a085",
    "#d35400",
    "#27ae60",
    "#7f8c8d",
    "#2980b9",
]


def get_or_create_team(db: Session, name: str) -> Team:
    team = db.scalar(select(Team).where(Team.name == name))
    if team is not None:
        return team
    used = {t.color for t in db.scalars(select(Team)).all()}
    color = next((c for c in TEAM_COLORS if c not in used), TEAM_COLORS[0])
    team = Team(name=name, color=color)
    db.add(team)
    db.flush()
    return team
