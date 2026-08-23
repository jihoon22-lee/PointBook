import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.models import Person, Team
from app.services.teams import TEAM_COLORS
from app.template_utils import render

router = APIRouter(prefix="/teams", dependencies=[Depends(require_login)], tags=["teams"])
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
TEAM_MEMBER_SORT_KEYS = frozenset({"name", "point_no", "personal_no", "grade", "status", "total"})


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


def _team_page_context(
    db: Session,
    error: str | None = None,
    selected_color: str = TEAM_COLORS[0],
) -> dict[str, object]:
    return {
        "team_summaries": _team_summaries(db),
        "selected_color": selected_color,
        "error": error,
    }


def _team_member_order(sort_key: str, direction: str) -> list[Any]:
    total_balance = Person.current_carry_balance + Person.current_amount
    expressions = {
        "name": Person.name,
        "point_no": Person.point_no,
        "personal_no": Person.personal_no,
        "grade": Person.grade,
        "status": case((Person.status == "active", 0), else_=1),
        "total": total_balance,
    }
    expression = expressions[sort_key]
    order: list[Any] = []
    if sort_key in {"personal_no", "grade"}:
        order.append(case((expression.is_(None), 1), else_=0))
    order.append(expression.desc() if direction == "desc" else expression.asc())
    order.extend((Person.name.asc(), Person.id.asc()))
    return order


@router.get("")
def list_teams(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(request, "teams.html", _team_page_context(db))


@router.get("/{team_id}")
def team_detail(
    team_id: int,
    request: Request,
    sort: str = "status",
    direction: str = Query("asc", alias="dir"),
    db: Session = Depends(get_db),
) -> Response:
    team = db.get(Team, team_id)
    if team is None:
        return RedirectResponse("/teams", status_code=303)
    sort = sort if sort in TEAM_MEMBER_SORT_KEYS else "status"
    direction = direction if direction in {"asc", "desc"} else "asc"
    members = list(
        db.scalars(
            select(Person)
            .where(Person.team_id == team.id, Person.account_type == "person")
            .order_by(*_team_member_order(sort, direction))
        ).all()
    )
    return render(
        request,
        "team_detail.html",
        {"team": team, "members": members, "sort": sort, "direction": direction},
    )


@router.post("")
def create_team(
    request: Request,
    name: str = Form(...),
    color: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    name = name.strip()
    color = color.strip().lower()
    if not name:
        return render(
            request,
            "teams.html",
            _team_page_context(
                db,
                "팀 이름을 입력해 주세요.",
                color if COLOR_PATTERN.fullmatch(color) else TEAM_COLORS[0],
            ),
            400,
        )
    if COLOR_PATTERN.fullmatch(color) is None:
        return render(
            request,
            "teams.html",
            _team_page_context(db, "올바른 색상을 선택해 주세요."),
            400,
        )
    if db.scalar(select(Team).where(Team.name == name)) is not None:
        return render(
            request,
            "teams.html",
            _team_page_context(db, f"팀 '{name}' 은(는) 이미 존재합니다.", color),
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
