from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.services import stats
from app.template_utils import render

router = APIRouter(prefix="/dashboard", dependencies=[Depends(require_login)], tags=["dashboard"])


@router.get("")
def dashboard(request: Request, month: str = "", db: Session = Depends(get_db)) -> Response:
    months = stats.available_months(db)
    selected = month if month in months else (months[0] if months else "")

    summary = stats.month_summary(db, selected) if selected else None
    teams = stats.team_summary(db, selected) if selected else []
    persons = stats.person_summary(db, selected) if selected else []
    trend_data = stats.trend(db)

    chart = {
        "labels": [s.month for s in trend_data],
        "amount": [s.total_amount for s in trend_data],
        "usage": [s.total_usage for s in trend_data],
        "balance": [s.total_balance for s in trend_data],
    }

    return render(
        request,
        "dashboard.html",
        {
            "months": months,
            "selected": selected,
            "summary": summary,
            "teams": teams,
            "persons": persons,
            "chart": chart,
            "has_data": bool(selected),
        },
    )
