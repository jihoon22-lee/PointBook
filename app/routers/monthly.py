import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.ai.factory import get_provider
from app.auth import require_login
from app.db import get_db
from app.models import MonthlySnapshot, Person
from app.services import stats
from app.services.balance import build_balance_records, create_monthly_snapshot, previous_total
from app.services.dates import current_month
from app.services.parsing import _to_int, parse_pasted
from app.services.sync import RequestRow, SyncAnalysis, analyze, apply_analysis
from app.template_utils import render

router = APIRouter(prefix="/monthly", dependencies=[Depends(require_login)], tags=["monthly"])

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _parse_row_fields(form: FormData) -> list[RequestRow]:
    rows: list[RequestRow] = []
    i = 0
    while f"personal_no_{i}" in form:
        personal_no = str(form.get(f"personal_no_{i}", "")).strip()
        name = str(form.get(f"name_{i}", "")).strip()
        if personal_no and name:
            rows.append(
                RequestRow(
                    personal_no=personal_no,
                    name=name,
                    team=str(form.get(f"team_{i}", "")).strip(),
                    grade=str(form.get(f"grade_{i}", "")).strip(),
                    amount=_to_int(str(form.get(f"amount_{i}", "0"))),
                    note=str(form.get(f"note_{i}", "")).strip(),
                )
            )
        i += 1
    return rows


def _parse_carry_fields(form: FormData) -> dict[str, int]:
    carries: dict[str, int] = {}
    for key, value in form.items():
        if key.startswith("carry_"):
            person_key = key[len("carry_") :]
            carries[person_key] = _to_int(str(value))
    return carries


@router.get("")
def monthly_home(request: Request, db: Session = Depends(get_db)) -> Response:
    months = stats.available_months(db)
    summary = [stats.month_summary(db, month) for month in months]
    return render(
        request,
        "monthly.html",
        {"summary": summary, "month": current_month(), "done": request.query_params.get("done")},
    )


@router.post("/upload")
async def upload(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    month = str(form.get("month", "")).strip() or current_month()
    rows: list[RequestRow] = []
    pasted = str(form.get("pasted", "")).strip()
    if pasted:
        rows = parse_pasted(pasted)
    file = form.get("file")
    if isinstance(file, StarletteUploadFile) and file.filename:
        provider = get_provider()
        rows = provider.extract_table(await file.read(), file.filename)
    if not rows:
        months = stats.available_months(db)
        summary = [stats.month_summary(db, m) for m in months]
        return render(
            request,
            "monthly.html",
            {
                "month": month,
                "error": "인식된 인원이 없습니다. 사진을 다시 업로드하거나 표를 붙여넣기해 주세요.",
                "summary": summary,
            },
            400,
        )
    analysis = analyze(db, rows)
    prev_totals: dict[str, int] = {}
    for change in analysis.changes:
        prev = 0
        if change.person_id is not None:
            prev = previous_total(db, change.person_id, month)
        prev_totals[f"{change.personal_no}|{change.name}"] = prev
    return render(
        request,
        "review.html",
        {"rows": rows, "analysis": analysis, "month": month, "prev_totals": prev_totals},
    )


@router.post("/confirm")
async def confirm(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    month = str(form.get("month", "")).strip()
    empty = {"rows": [], "analysis": SyncAnalysis(changes=[]), "month": month}
    if not MONTH_RE.match(month):
        return render(
            request,
            "review.html",
            {**empty, "error": "월 형식이 올바르지 않습니다. (YYYY-MM)"},
            400,
        )
    if db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month)) is not None:
        return render(
            request, "review.html", {**empty, "error": f"{month} 월은 이미 처리되었습니다."}, 400
        )

    rows = _parse_row_fields(form)
    if not rows:
        return render(request, "review.html", {**empty, "error": "인원 행이 없습니다."}, 400)
    carries = _parse_carry_fields(form)

    analysis = analyze(db, rows)
    if not analysis.changes:
        return render(request, "review.html", {**empty, "error": "동기화할 인원이 없습니다."}, 400)

    apply_analysis(db, analysis)
    db.flush()

    carry_map: dict[int, int] = {}
    amount_map: dict[int, int] = {}
    for change in analysis.changes:
        person = db.scalar(
            select(Person).where(
                Person.personal_no == change.personal_no, Person.name == change.name
            )
        )
        if person is None:
            continue
        key = f"{change.personal_no}|{change.name}"
        carry_map[person.id] = carries.get(key, 0)
        amount_map[person.id] = sum(
            r.amount for r in rows if r.personal_no == change.personal_no and r.name == change.name
        )

    records = build_balance_records(db, month, carry_map, amount_map)
    create_monthly_snapshot(db, month, records)
    return RedirectResponse("/monthly?done=1", status_code=303)
