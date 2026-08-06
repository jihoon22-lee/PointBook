from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.models import Person, Team
from app.services.teams import TEAM_COLORS
from app.template_utils import render

router = APIRouter(prefix="/teams", dependencies=[Depends(require_login)], tags=["teams"])


@router.get("")
def list_teams(request: Request, db: Session = Depends(get_db)) -> Response:
    teams = db.scalars(select(Team).order_by(Team.name)).all()
    return render(request, "teams.html", {"teams": teams, "colors": TEAM_COLORS})


@router.get("/{team_id}")
def team_detail(team_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/teams", status_code=303)
    members = list(
        db.scalars(
            select(Person)
            .where(Person.team_id == team.id)
            .order_by(Person.status.desc(), Person.name)
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
        teams = db.scalars(select(Team).order_by(Team.name)).all()
        return render(
            request,
            "teams.html",
            {"teams": teams, "colors": TEAM_COLORS, "error": "팀 이름을 입력해 주세요."},
            400,
        )
    if db.scalar(select(Team).where(Team.name == name)) is not None:
        teams = db.scalars(select(Team).order_by(Team.name)).all()
        return render(
            request,
            "teams.html",
            {
                "teams": teams,
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
