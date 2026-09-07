import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.services import stats
from app.services.dates import current_month
from app.services.xlsx import report_workbook
from app.template_utils import render

router = APIRouter(prefix="/dashboard", dependencies=[Depends(require_login)], tags=["dashboard"])


@router.get("")
def dashboard(
    request: Request,
    month: str = "",
    scope: str = "observed",
    account_type: str = "person",
    person_id: int | None = None,
    team_name: str | None = None,
    operation_id: str = "",
    sort_by: str = "name",
    direction: str = "asc",
    db: Session = Depends(get_db),
) -> Response:
    months = stats.available_months(db)
    selected = month or (months[0] if months else current_month())
    requested_operation = None
    try:
        if operation_id:
            if not re.fullmatch(r"[0-9]{1,19}", operation_id) or int(operation_id) > 2**63 - 1:
                raise ValueError("정정판 작업 번호는 0 이상의 정수여야 합니다.")
            requested_operation = int(operation_id)
        cutoff = stats.report_cutoff(db) if requested_operation is None else requested_operation
        result = stats.report(
            db,
            selected,
            scope,
            account_type,
            person_id,
            team_name if team_name else None,
            cutoff,
        )
        stats.sort_report(result, sort_by, direction)
        trend_data = stats.trend(
            db,
            scope=scope,
            account_type=account_type,
            operation_id=cutoff,
            person_id=person_id,
            team_name=team_name or None,
        )
    except ValueError as exc:
        return render(
            request,
            "dashboard.html",
            {
                "error": str(exc),
                "months": months,
                "selected": selected,
                "has_data": False,
                "scope": scope,
                "account_type": account_type,
                "operation_id": operation_id,
            },
            400,
        )
    if selected not in months:
        months = sorted([*months, selected], reverse=True)
    chart = {
        "labels": [s.month for s in trend_data],
        "amount": [s.total_amount if s.processed_count else None for s in trend_data],
        "usage": [s.total_usage if s.processed_count else None for s in trend_data],
        "balance": [s.total_balance if s.known_balance_count else None for s in trend_data],
    }
    return render(
        request,
        "dashboard.html",
        {
            "months": months,
            "selected": selected,
            "summary": result.summary,
            "teams": result.teams,
            "persons": result.rows,
            "report": result,
            "chart": chart,
            "has_data": bool(result.rows or result.unclassified_rows),
            "scope": scope,
            "account_type": account_type,
            "team_name": team_name or "",
            "person_id": person_id,
            "operation_id": requested_operation,
            "sort_by": sort_by,
            "direction": direction,
            "export_url": "/dashboard/export.xlsx?"
            + urlencode(
                {
                    "month": selected,
                    "scope": scope,
                    "account_type": account_type,
                    "team_name": team_name or "",
                    "operation_id": cutoff,
                    "sort_by": sort_by,
                    "direction": direction,
                    **({"person_id": person_id} if person_id else {}),
                }
            ),
        },
    )


@router.get("/export.xlsx")
def export_report(
    request: Request,
    month: str,
    scope: str = "observed",
    account_type: str = "person",
    person_id: int | None = None,
    team_name: str = "",
    operation_id: str = "",
    sort_by: str = "name",
    direction: str = "asc",
    db: Session = Depends(get_db),
) -> Response:
    try:
        if operation_id and (
            not re.fullmatch(r"[0-9]{1,19}", operation_id) or int(operation_id) > 2**63 - 1
        ):
            raise ValueError("정정판 작업 번호는 0 이상의 정수여야 합니다.")
        cutoff = int(operation_id) if operation_id else stats.report_cutoff(db)
        result = stats.sort_report(
            stats.report(db, month, scope, account_type, person_id, team_name or None, cutoff),
            sort_by,
            direction,
        )
        data = report_workbook(
            result, sort_by=sort_by, direction=direction, team_name=team_name, person_id=person_id
        )
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="pointbook-report-{result.month}-{scope}-{account_type}.xlsx"',
                "Cache-Control": "no-store",
            },
        )
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(400, str(exc)) from exc
