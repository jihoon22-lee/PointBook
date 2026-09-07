from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.auth import require_login
from app.config import get_settings
from app.db import get_db
from app.logging import get_logger
from app.models import Person
from app.services import stats
from app.services.backup import backup_database
from app.services.balance import build_balance_records, create_monthly_snapshot
from app.services.dates import current_month
from app.services.parsing import MAX_REQUEST_ROWS, RawRequestRow, parse_pasted_raw
from app.services.review import (
    Review,
    carry_values,
    deactivated_from_form,
    matches_token,
    raw_rows_from_form,
    review_rows,
)
from app.services.sync import ACTION_DEACTIVATED, RequestRow, apply_analysis
from app.services.vision import extract_image
from app.template_utils import render

router = APIRouter(prefix="/monthly", dependencies=[Depends(require_login)], tags=["monthly"])
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic"}


async def monthly_form(request: Request) -> FormData:
    return await request.form(max_files=1, max_fields=MAX_REQUEST_ROWS * 14 + 20)


def _parse_row_fields(form: FormData) -> list[RequestRow]:
    return [r.validated() for r in raw_rows_from_form(form)]


@router.get("")
def monthly_home(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(
        request,
        "monthly.html",
        {
            "summary": [stats.month_summary(db, m) for m in stats.available_months(db)],
            "month": current_month(),
            "done": request.query_params.get("done"),
            "ai_provider": get_settings().ai_provider,
        },
    )


def _error_response(
    request: Request,
    db: Session,
    month: str,
    message: str,
    pasted: str = "",
) -> Response:
    return render(
        request,
        "monthly.html",
        {
            "month": month,
            "error": message,
            "pasted": pasted,
            "ai_provider": get_settings().ai_provider,
            "summary": [stats.month_summary(db, m) for m in stats.available_months(db)],
        },
        400,
    )


def review_response(
    request: Request,
    month: str,
    review: Review,
    *,
    deactivated: dict[str, str] | None = None,
    expected_count: str = "",
    expected_amount: str = "",
    message: str = "",
    status: int = 200,
) -> Response:
    deactivated = deactivated or {}
    needed = {c.point_no for c in review.analysis.changes if c.action == ACTION_DEACTIVATED}
    obsolete = {point: value for point, value in deactivated.items() if point not in needed}
    return render(
        request,
        "review.html",
        {
            "rows": review.raw_rows,
            "analysis": review.analysis,
            "month": month,
            "errors": review.errors,
            "warnings": review.warnings,
            "review_token": review.token,
            "prev_totals": review.prev_totals,
            "request_amount": review.request_amount,
            "previous_count": review.previous_count,
            "previous_amount": review.previous_amount,
            "expected_count": expected_count,
            "expected_amount": expected_amount,
            "deactivated_carries": deactivated,
            "obsolete_carries": obsolete,
            "error": message,
        },
        status,
    )


@router.post("/upload")
async def upload(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await monthly_form(request)
    month = str(form.get("month", ""))
    pasted = str(form.get("pasted", ""))
    rows: list[RawRequestRow] = []
    file = form.get("file")
    if pasted.strip() and isinstance(file, StarletteUploadFile) and file.filename:
        return _error_response(
            request, db, month, "사진과 붙여넣기 중 하나만 선택해 주세요.", pasted
        )
    try:
        if pasted.strip():
            rows = parse_pasted_raw(pasted)
        if isinstance(file, StarletteUploadFile) and file.filename:
            settings = get_settings()
            ext = f".{file.filename.lower().rsplit('.', 1)[-1]}" if "." in file.filename else ""
            if ext not in ALLOWED_IMAGE_EXTS:
                raise ValueError("지원하지 않는 이미지 형식입니다. (png, jpg, jpeg, webp, heic)")
            data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
            if len(data) > settings.max_upload_mb * 1024 * 1024:
                raise ValueError(f"파일이 너무 큽니다. (최대 {settings.max_upload_mb}MB)")
            rows = await extract_image(data, file.filename)
        if not rows:
            raise ValueError(
                "인식된 인원이 없습니다. 사진을 다시 업로드하거나 표를 붙여넣기해 주세요."
            )
    except ValueError as exc:
        return _error_response(request, db, month, str(exc), pasted)
    finally:
        if isinstance(file, StarletteUploadFile):
            await file.close()
    # 기존 공용계정 유형은 DB에서 확인한다. 신규 유형은 검수 화면에서 명시한다.
    shared_points = set(db.scalars(select(Person.point_no).where(Person.account_type == "shared")))
    for row in rows:
        if row.point_no.replace(" ", "").replace("-", "") in shared_points:
            row.account_type = "shared"
    review = review_rows(db, month, rows)
    return review_response(request, month, review, status=400 if review.errors else 200)


@router.post("/review")
async def review(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await monthly_form(request)
    month = str(form.get("month", ""))
    try:
        rows = raw_rows_from_form(form)
    except ValueError as exc:
        return _error_response(request, db, month, str(exc))
    expected_count, expected_amount = (
        str(form.get("expected_count", "")),
        str(form.get("expected_amount", "")),
    )
    result = review_rows(db, month, rows, expected_count, expected_amount)
    return review_response(
        request,
        month,
        result,
        deactivated=deactivated_from_form(form),
        expected_count=expected_count,
        expected_amount=expected_amount,
        status=400 if result.errors else 200,
    )


@router.post("/confirm")
async def confirm(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await monthly_form(request)
    month = str(form.get("month", ""))
    expected_count, expected_amount = (
        str(form.get("expected_count", "")),
        str(form.get("expected_amount", "")),
    )
    try:
        rows = raw_rows_from_form(form)
    except ValueError as exc:
        return _error_response(request, db, month, str(exc))
    deactivated = deactivated_from_form(form)
    result = review_rows(db, month, rows, expected_count, expected_amount)

    def response(message: str = "", status: int = 400) -> Response:
        return review_response(
            request,
            month,
            result,
            deactivated=deactivated,
            expected_count=expected_count,
            expected_amount=expected_amount,
            message=message,
            status=status,
        )

    if result.errors:
        return response()
    carries, errors = carry_values(result, deactivated)
    if errors:
        result.errors.update(errors)
        return response("모든 처리 대상의 이월 잔액을 확인해 주세요.")
    token = str(form.get("review_token", ""))
    if not matches_token(token, result.digest):
        return response(
            "목록·월·기준 정보가 변경되었거나 검수가 만료되었습니다. 새 변경 예상을 확인하고 다시 확정하세요.",
            409,
        )
    if result.warnings and form.get("ack_warnings") != "yes":
        return response("처리 월과 누락·합계 경고를 확인한 뒤 확인란을 선택해 주세요.")
    # SQLite writer를 먼저 직렬화하고 승인한 상태를 같은 트랜잭션 안에서 재검증한다.
    db.rollback()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        locked = review_rows(db, month, rows, expected_count, expected_amount)
        if locked.errors or not matches_token(token, locked.digest):
            result = locked
            db.rollback()
            return response("다른 작업으로 기준이 변경되었습니다. 다시 검수해 주세요.", 409)
        result = locked
        carries, locked_errors = carry_values(result, deactivated)
        if locked_errors:
            result.errors.update(locked_errors)
            db.rollback()
            return response()
        backup_database()
        apply_analysis(db, result.analysis)
        db.flush()
        people = {p.point_no: p for p in db.scalars(select(Person)).all()}
        carry_map = {people[c.point_no].id: carries[c.point_no] for c in result.analysis.changes}
        amount_map = {people[c.point_no].id: c.amount for c in result.analysis.changes}
        records = build_balance_records(db, month, carry_map, amount_map)
        by_id = {p.id: p for p in people.values()}
        for record in records:
            person = by_id[record.person_id]
            person.current_carry_balance, person.current_amount = (
                record.carry_balance,
                record.amount,
            )
        create_monthly_snapshot(db, month, records, commit=False)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return response(str(exc))
    except (IntegrityError, OperationalError):
        db.rollback()
        return response("다른 작업과 충돌했습니다. 다시 검수한 뒤 확정해 주세요.", 409)
    except Exception:  # noqa: BLE001
        db.rollback()
        get_logger().error("월간 확정 실패: 변경을 롤백했습니다.")
        return response(
            "확정 처리 중 오류가 발생했습니다. 입력을 보존했습니다. 다시 시도해 주세요.", 500
        )
    return RedirectResponse("/monthly?done=1", status_code=303)
