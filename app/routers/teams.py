from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.models import Person, Team
from app.services.teams import TEAM_COLORS
from app.template_utils import render

router = APIRouter(prefix="/teams", dependencies=[Depends(require_login)], tags=["teams"])


@dataclass(frozen=True)
class TeamSummary:
    team: Team
    total_count: int
    active_count: int
    inactive_count: int


def _team_summaries(db: Session) -> list[TeamSummary]:
    rows = db.execute(
        select(
            Team,
            func.count(Person.id),
            func.sum(case((Person.status == "active", 1), else_=0)),
            func.sum(case((Person.status == "inactive", 1), else_=0)),
        )
        .outerjoin(
            Person,
            and_(Person.team_id == Team.id, Person.account_type == "person"),
        )
        .group_by(Team.id)
        .order_by(Team.name)
    ).all()
    return [
        TeamSummary(
            team=team,
            total_count=total_count,
            active_count=active_count,
            inactive_count=inactive_count,
        )
        for team, total_count, active_count, inactive_count in rows
    ]


@router.get("")
def list_teams(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(
        request,
        "teams.html",
        {"team_summaries": _team_summaries(db), "colors": TEAM_COLORS},
    )


@router.get("/{team_id}")
def team_detail(team_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/teams", status_code=303)
    members = list(
        db.scalars(
            select(Person)
            .where(Person.team_id == team.id, Person.account_type == "person")
            .order_by(case((Person.status == "active", 0), else_=1), Person.name, Person.id)
        ).all()
    )
    return render(request, "team_detail.html", {"team": team, "members": members})


@router.post("")
def create_team(
    request: Request,
    name: str = Form(...),
    color: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    name = name.strip()
    if not name:
        return render(
            request,
            "teams.html",
            {
                "team_summaries": _team_summaries(db),
                "colors": TEAM_COLORS,
                "error": "팀 이름을 입력해 주세요.",
            },
            400,
        )
    if db.scalar(select(Team).where(Team.name == name)) is not None:
        return render(
            request,
            "teams.html",
            {
                "team_summaries": _team_summaries(db),
                "colors": TEAM_COLORS,
                "error": f"팀 '{name}' 은(는) 이미 존재합니다.",
            },
            400,
        )
    db.add(Team(name=name, color=color))
    db.commit()
    return RedirectResponse("/teams", status_code=303)


@router.post("/{team_id}/delete")
def delete_team(team_id: int, db: Session = Depends(get_db)) -> Response:
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/teams", status_code=303)
    for person in team.persons:
        person.team_id = None
    db.delete(team)
    db.commit()
    return RedirectResponse("/teams", status_code=303)
