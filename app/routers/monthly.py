import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.ai.factory import get_provider
from app.auth import require_login
from app.config import get_settings
from app.db import get_db
from app.logging import get_logger
from app.models import MonthlySnapshot, Person
from app.services import stats
from app.services.backup import backup_database
from app.services.balance import build_balance_records, create_monthly_snapshot, previous_total
from app.services.dates import current_month
from app.services.parsing import _to_int, parse_pasted
from app.services.sync import ACTION_DEACTIVATED, RequestRow, SyncAnalysis, analyze, apply_analysis
from app.template_utils import render

router = APIRouter(prefix="/monthly", dependencies=[Depends(require_login)], tags=["monthly"])

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic"}


def _parse_indexed_row_fields(form: FormData) -> list[tuple[int, RequestRow]]:
    rows: list[tuple[int, RequestRow]] = []
    i = 0
    row_fields = ("point_no", "personal_no", "name", "team", "grade", "amount", "note")
    while any(f"{field}_{i}" in form for field in row_fields):
        point_no = str(form.get(f"point_no_{i}", "")).strip()
        personal_no = str(form.get(f"personal_no_{i}", "")).strip()
        name = str(form.get(f"name_{i}", "")).strip()
        if personal_no and name:
            rows.append(
                (
                    i,
                    RequestRow(
                        point_no=point_no,
                        personal_no=personal_no,
                        name=name,
                        team=str(form.get(f"team_{i}", "")).strip(),
                        grade=str(form.get(f"grade_{i}", "")).strip(),
                        amount=_to_int(str(form.get(f"amount_{i}", "0"))),
                        note=str(form.get(f"note_{i}", "")).strip(),
                    ),
                )
            )
        i += 1
    return rows


def _parse_row_fields(form: FormData) -> list[RequestRow]:
    return [row for _, row in _parse_indexed_row_fields(form)]


def _required_carry(form: FormData, key: str) -> int:
    if key not in form or not str(form.get(key, "")).strip():
        raise ValueError("모든 처리 대상의 이월 잔액을 입력해 주세요.")
    return _to_int(str(form.get(key)))


def _parse_carry_fields(
    form: FormData,
    indexed_rows: list[tuple[int, RequestRow]],
    analysis: SyncAnalysis,
) -> dict[str, int]:
    carries: dict[str, int] = {}
    for index, row in indexed_rows:
        carries[row.point_no] = _required_carry(form, f"carry_{index}")
    for change in analysis.changes:
        if change.action == ACTION_DEACTIVATED:
            carries[change.point_no] = _required_carry(form, f"deactivated_carry_{change.point_no}")
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


def _error_response(request: Request, db: Session, month: str, message: str) -> Response:
    months = stats.available_months(db)
    summary = [stats.month_summary(db, m) for m in months]
    return render(
        request,
        "monthly.html",
        {"month": month, "error": message, "summary": summary},
        400,
    )


@router.post("/upload")
async def upload(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    month = str(form.get("month", "")).strip() or current_month()
    rows: list[RequestRow] = []
    pasted = str(form.get("pasted", "")).strip()
    if pasted:
        try:
            rows = parse_pasted(pasted)
        except ValueError as exc:
            return _error_response(request, db, month, str(exc))
    file = form.get("file")
    if isinstance(file, StarletteUploadFile) and file.filename:
        settings = get_settings()
        ext = f".{file.filename.lower().rsplit('.', 1)[-1]}" if "." in file.filename else ""
        if ext not in ALLOWED_IMAGE_EXTS:
            return _error_response(
                request, db, month, "지원하지 않는 이미지 형식입니다. (png, jpg, jpeg, webp, heic)"
            )
        data = await file.read()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            return _error_response(
                request, db, month, f"파일이 너무 큽니다. (최대 {settings.max_upload_mb}MB)"
            )
        provider = get_provider()
        try:
            rows = provider.extract_table(data, file.filename)
        except ValueError as exc:
            return _error_response(request, db, month, str(exc))
    if not rows:
        return _error_response(
            request,
            db,
            month,
            "인식된 인원이 없습니다. 사진을 다시 업로드하거나 표를 붙여넣기해 주세요.",
        )
    try:
        analysis = analyze(db, rows)
    except ValueError as exc:
        return _error_response(request, db, month, str(exc))
    prev_totals: dict[str, int] = {}
    for change in analysis.changes:
        prev = 0
        if change.person_id is not None:
            prev = previous_total(db, change.person_id, month)
            if prev == 0:
                person = db.get(Person, change.person_id)
                if person is not None:
                    prev = person.current_carry_balance
        prev_totals[change.point_no] = prev
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

    try:
        indexed_rows = _parse_indexed_row_fields(form)
        rows = [row for _, row in indexed_rows]
    except ValueError as exc:
        return render(request, "review.html", {**empty, "error": str(exc)}, 400)
    if not rows:
        return render(request, "review.html", {**empty, "error": "인원 행이 없습니다."}, 400)
    try:
        analysis = analyze(db, rows)
        carries = _parse_carry_fields(form, indexed_rows, analysis)
    except ValueError as exc:
        return render(request, "review.html", {**empty, "error": str(exc)}, 400)
    if not analysis.changes:
        return render(request, "review.html", {**empty, "error": "동기화할 인원이 없습니다."}, 400)

    try:
        backup_database()
        apply_analysis(db, analysis)
        db.flush()

        carry_map: dict[int, int] = {}
        amount_map: dict[int, int] = {}
        for change in analysis.changes:
            person = db.scalar(select(Person).where(Person.point_no == change.point_no))
            if person is None:
                continue
            carry_map[person.id] = carries.get(change.point_no, 0)
            amount_map[person.id] = sum(r.amount for r in rows if r.point_no == change.point_no)

        records = build_balance_records(db, month, carry_map, amount_map)
        for record in records:
            person = db.get(Person, record.person_id)
            if person is not None:
                person.current_carry_balance = record.carry_balance
                person.current_amount = record.amount
        create_monthly_snapshot(db, month, records, commit=False)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return render(request, "review.html", {**empty, "error": str(exc)}, 400)
    except Exception:  # noqa: BLE001 — 예상 밖 오류도 롤백 후 친절한 메시지로 안내
        db.rollback()
        get_logger().exception("월간 확정 처리 실패")
        return render(
            request,
            "review.html",
            {**empty, "error": "확정 처리 중 오류가 발생했습니다. 다시 시도해 주세요."},
            500,
        )
    return RedirectResponse("/monthly?done=1", status_code=303)
