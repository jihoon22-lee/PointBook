import re
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.auth import require_login
from app.config import get_settings
from app.db import get_db
from app.logging import get_logger
from app.models import MonthlySnapshot, Person
from app.services import stats
from app.services.backup import backup_database
from app.services.balance import build_balance_records, create_monthly_snapshot
from app.services.dates import current_month, validate_month
from app.services.drafts import (
    DraftError,
    confirm_draft,
    draft_payload,
    draft_state,
    payload_from_form,
    save_draft,
    verify_draft_for_confirm,
)
from app.services.history import profile_for_person
from app.services.ledger import LedgerConflict, add_operation, find_replay
from app.services.parsing import MAX_REQUEST_ROWS, RawRequestRow, parse_pasted_raw
from app.services.request_profiles import choose, reset_target
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
from app.services.xlsx import FORM_VERSION, extract_request, request_template
from app.template_utils import render

router = APIRouter(prefix="/monthly", dependencies=[Depends(require_login)], tags=["monthly"])
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".xlsx"}


async def monthly_form(request: Request) -> FormData:
    return await request.form(max_files=1, max_fields=MAX_REQUEST_ROWS * 16 + 50)


def _parse_row_fields(form: FormData) -> list[RequestRow]:
    return [r.validated() for r in raw_rows_from_form(form)]


@router.get("")
def monthly_home(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(
        request,
        "monthly.html",
        {
            "summary": list(
                reversed(stats.trend(db, account_type="all", operation_id=stats.report_cutoff(db)))
            ),
            "month_statuses": {
                month: status
                for month, status in db.execute(
                    select(MonthlySnapshot.month, MonthlySnapshot.status)
                )
            },
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
            "month_statuses": {
                month: status
                for month, status in db.execute(
                    select(MonthlySnapshot.month, MonthlySnapshot.status)
                )
            },
            "error": message,
            "pasted": pasted,
            "ai_provider": get_settings().ai_provider,
            "summary": list(
                reversed(stats.trend(db, account_type="all", operation_id=stats.report_cutoff(db)))
            ),
        },
        400,
    )


def review_response(
    request: Request,
    month: str,
    review: Review,
    *,
    request_key: str = "",
    draft_info: dict[str, Any] | None = None,
    input_source: str = "manual",
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
            **(draft_info or {}),
            "input_source": input_source,
            "draft_keep_days": get_settings().draft_keep_days,
            "rows": review.raw_rows,
            "candidates": review.candidates,
            "pending_links": review.pending_links,
            "row_states": review.row_states,
            "profile_differences": review.profile_differences,
            "pending_profiles": review.pending_profiles,
            "other_errors": {
                key: value
                for key, value in review.errors.items()
                if key not in review.pending_links
            },
            "analysis": review.analysis,
            "row_changes": {
                change.point_no: {
                    "action": change.action,
                    "team_changed": change.team_changed,
                    "profile_changed": change.profile_changed,
                }
                for change in review.analysis.changes
            },
            "month": month,
            "errors": review.errors,
            "warnings": review.warnings,
            "review_token": review.token,
            "request_key": request_key or uuid.uuid4().hex,
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
    source = "paste" if pasted.strip() else "image"
    file = form.get("file")
    if pasted.strip() and isinstance(file, StarletteUploadFile) and file.filename:
        return _error_response(
            request, db, month, "파일과 붙여넣기 중 하나만 선택해 주세요.", pasted
        )
    try:
        if pasted.strip():
            rows = parse_pasted_raw(pasted)
        if isinstance(file, StarletteUploadFile) and file.filename:
            settings = get_settings()
            ext = f".{file.filename.lower().rsplit('.', 1)[-1]}" if "." in file.filename else ""
            if ext not in ALLOWED_IMAGE_EXTS:
                raise ValueError(
                    "지원하지 않는 파일 형식입니다. (png, jpg, jpeg, webp, heic, 표준 xlsx)"
                )
            data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
            if len(data) > settings.max_upload_mb * 1024 * 1024:
                raise ValueError(f"파일이 너무 큽니다. (최대 {settings.max_upload_mb}MB)")
            if ext == ".xlsx":
                parsed = await extract_request(data, file.filename)
                if parsed.month != month:
                    raise ValueError(
                        f"파일 처리 월({parsed.month})과 선택 월({month})이 다릅니다. 원본을 확인해 주세요."
                    )
                rows, source = parsed.rows, "xlsx"
            else:
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
    # 표준 XLSX의 명시적 유형은 보존한다. 유형 열 없는 입력만 DB 공용 정보를 보완한다.
    if source != "xlsx":
        shared_points = set(
            db.scalars(select(Person.point_no).where(Person.account_type == "shared"))
        )
        for row in rows:
            if row.point_no.replace(" ", "").replace("-", "") in shared_points:
                row.account_type = "shared"
    return store_review_response(request, db, month, rows, source=source)


@router.post("/link")
async def link_person(request: Request, db: Session = Depends(get_db)) -> Response:
    """선택한 인원의 포인트번호를 보완하고 프로필 차이는 별도 확인한다."""
    form = await monthly_form(request)
    month = str(form.get("month", ""))
    try:
        rows = raw_rows_from_form(form)
    except ValueError as exc:
        return _error_response(request, db, month, str(exc))
    try:
        row_id = str(form.get("link_row", ""))
        targets = [(index, row) for index, row in enumerate(rows) if row.row_id == row_id]
        if len(targets) != 1:
            raise ValueError("연결할 요청서 행을 확인하세요.")
        index, row = targets[0]
        selection = str(form.get(f"link_person_{index}", ""))
        if not re.fullmatch(r"[1-9][0-9]{0,17}:[1-9][0-9]{0,17}", selection):
            raise ValueError("연결할 기존 인원을 선택하세요.")
        person_id, version = (int(value) for value in selection.split(":"))
        person = db.scalar(
            select(Person).options(joinedload(Person.team)).where(Person.id == person_id)
        )
        if person is None or person.version != version:
            raise ValueError("선택한 인원 정보가 변경되었습니다. 다시 검수하여 후보를 확인하세요.")
        if row.point_no != person.point_no:
            reset_target(row)
        row.point_no = person.point_no
        row.link_state = f"manual:{person.point_no}"
        # 연결 이전에 입력한 잔액이 다른 사람에게 적용되지 않도록 다시 확인받는다.
        row.carry = ""
    except ValueError as exc:
        result = review_rows(db, month, rows)
        result.errors["link"] = str(exc)
        result.token = ""
        return review_response(
            request,
            month,
            result,
            request_key=str(form.get("request_key", "")),
            draft_info={
                "draft_id": str(form.get("draft_id", "")),
                "draft_version": str(form.get("draft_version", "")),
            },
            input_source=str(form.get("input_source", "manual")),
            deactivated=deactivated_from_form(form),
            expected_count=str(form.get("expected_count", "")),
            expected_amount=str(form.get("expected_amount", "")),
            status=400,
        )
    return store_review_response(request, db, month, rows, form=form)


@router.post("/choose")
@router.post("/new")
async def choose_profile(request: Request, db: Session = Depends(get_db)) -> Response:
    """화면에 표시된 비교에 대한 선택만 초안에 저장한다."""
    form = await monthly_form(request)
    month = str(form.get("month", ""))
    try:
        rows = raw_rows_from_form(form)
    except ValueError as exc:
        return _error_response(request, db, month, str(exc))
    try:
        if request.url.path.endswith("/new"):
            row_id = str(form.get("new_row", ""))
            field, side = "", ""
        else:
            parts = str(form.get("profile_choice", "")).split(":")
            if len(parts) != 3:
                raise ValueError("선택할 항목을 확인하세요.")
            row_id, field, side = parts
        targets = [row for row in rows if row.row_id == row_id]
        if len(targets) != 1:
            raise ValueError("선택할 요청서 행을 확인하세요.")
        row = targets[0]
        if request.url.path.endswith("/new"):
            reset_target(row)
            row.point_no, row.link_state, row.carry = "", "new", ""
        else:
            person = db.scalar(
                select(Person)
                .options(joinedload(Person.team))
                .where(Person.point_no == row.point_no)
            )
            if person is None:
                raise ValueError("연결된 인원이 변경되었습니다. 다시 연결하세요.")
            choose(row, person, field, side)
    except ValueError as exc:
        result = review_rows(db, month, rows)
        result.token = ""
        return review_response(
            request,
            month,
            result,
            request_key=str(form.get("request_key", "")),
            draft_info={
                "draft_id": str(form.get("draft_id", "")),
                "draft_version": str(form.get("draft_version", "")),
            },
            input_source=str(form.get("input_source", "manual")),
            deactivated=deactivated_from_form(form),
            expected_count=str(form.get("expected_count", "")),
            expected_amount=str(form.get("expected_amount", "")),
            message=str(exc),
            status=409,
        )
    return store_review_response(request, db, month, rows, form=form)


@router.post("/review")
async def review(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await monthly_form(request)
    month = str(form.get("month", ""))
    try:
        rows = raw_rows_from_form(form, clear_source_issues=True)
    except ValueError as exc:
        return _error_response(request, db, month, str(exc))
    return store_review_response(request, db, month, rows, form=form)


def store_review_response(
    request: Request,
    db: Session,
    month: str,
    rows: list[RawRequestRow],
    *,
    form: FormData | None = None,
    source: str = "manual",
) -> Response:
    form = form or FormData()
    source = str(form.get("input_source", source))
    expected_count = str(form.get("expected_count", ""))
    expected_amount = str(form.get("expected_amount", ""))
    deactivated = deactivated_from_form(form)
    key = str(form.get("request_key", "")) or uuid.uuid4().hex
    result = review_rows(db, month, rows, expected_count, expected_amount)
    info: dict[str, Any] = {
        "draft_id": str(form.get("draft_id", "")),
        "draft_version": str(form.get("draft_version", "")),
    }
    status, message = (400 if result.errors else 200), ""
    try:
        draft = save_draft(
            db,
            owner_id=int(request.session["admin_id"]),
            payload=draft_payload(
                month,
                result.raw_rows,
                deactivated=deactivated,
                expected_count=expected_count,
                expected_amount=expected_amount,
                source=source,
                review_token=result.token,
            ),
            request_key=key,
            draft_id=info["draft_id"],
            version=info["draft_version"],
        )
        info = draft_state(draft)
    except DraftError as exc:
        status, message = exc.status, str(exc)
    except Exception:  # noqa: BLE001 - 초안 실패는 원문을 보존하고 안전하게 알린다.
        db.rollback()
        status, message = (
            500,
            "초안을 저장하지 못했습니다. 화면의 입력을 보존했습니다. 다시 저장하세요.",
        )
    if status == 200 and info.get("draft_saved_at"):
        return RedirectResponse("/drafts/" + str(info["draft_id"]), status_code=303)
    return review_response(
        request,
        month,
        result,
        request_key=key,
        draft_info=info,
        input_source=source,
        deactivated=deactivated,
        expected_count=expected_count,
        expected_amount=expected_amount,
        message=message,
        status=status,
    )


@router.post("/start")
async def start_monthly(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await monthly_form(request)
    return store_review_response(
        request, db, str(form.get("month", current_month())), [RawRequestRow()], source="manual"
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
    request_key = str(form.get("request_key", ""))
    payload = {
        "month": month,
        "rows": [{k: v for k, v in asdict(row).items() if k != "source_line"} for row in rows],
        "deactivated": deactivated,
        "expected_count": expected_count,
        "expected_amount": expected_amount,
    }
    replay_error = ""
    try:
        replay = find_replay(db, request_key, "monthly", payload)
        if replay:
            return RedirectResponse(replay.result_url, status_code=303)
    except ValueError as exc:
        replay_error = str(exc)
    result = review_rows(db, month, rows, expected_count, expected_amount)

    def response(message: str = "", status: int = 400) -> Response:
        return review_response(
            request,
            month,
            result,
            request_key=request_key,
            draft_info={
                "draft_id": str(form.get("draft_id", "")),
                "draft_version": str(form.get("draft_version", "")),
            },
            input_source=str(form.get("input_source", "manual")),
            deactivated=deactivated,
            expected_count=expected_count,
            expected_amount=expected_amount,
            message=message,
            status=status,
        )

    if result.pending_profiles:
        return response(
            "기존 정보와 다른 항목의 값을 먼저 선택하세요.",
            409 if form.get("review_token") else 400,
        )
    if result.errors:
        if "month" in result.errors and form.get("review_token"):
            try:
                validate_month(month)
            except ValueError:
                pass
            else:
                return response(
                    "다른 작업으로 처리 월의 기준이 변경되었습니다. 다시 검수하세요.", 409
                )
        return response()
    if replay_error:
        return response(replay_error, 409)
    token = str(form.get("review_token", ""))
    if not matches_token(token, result.digest):
        return response(
            "목록·월·기준 정보가 변경되었거나 검수가 만료되었습니다. 새 변경 예상을 확인하고 다시 확정하세요.",
            409,
        )
    carries, errors = carry_values(result, deactivated)
    if errors:
        result.errors.update(errors)
        return response("모든 처리 대상의 이월 잔액을 확인해 주세요.")
    if result.warnings and form.get("ack_warnings") != "yes":
        return response("처리 월과 누락·합계 경고를 확인한 뒤 확인란을 선택해 주세요.")
    # SQLite writer를 먼저 직렬화하고 승인한 상태를 같은 트랜잭션 안에서 재검증한다.
    db.rollback()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        replay = find_replay(db, request_key, "monthly", payload)
        if replay:
            db.rollback()
            return RedirectResponse(replay.result_url, status_code=303)
        locked = review_rows(db, month, rows, expected_count, expected_amount)
        if locked.errors or locked.pending_profiles or not matches_token(token, locked.digest):
            result = locked
            db.rollback()
            return response("다른 작업으로 기준이 변경되었습니다. 다시 검수해 주세요.", 409)
        result = locked
        carries, locked_errors = carry_values(result, deactivated)
        if locked_errors:
            result.errors.update(locked_errors)
            db.rollback()
            return response()
        draft = verify_draft_for_confirm(
            db,
            draft_id=str(form.get("draft_id", "")),
            owner_id=int(request.session["admin_id"]),
            version=str(form.get("draft_version", "")),
            request_key=request_key,
        )
        backup_database()
        before = {
            p.point_no: profile_for_person(p)
            for p in db.scalars(select(Person).options(joinedload(Person.team))).all()
        }
        operation = add_operation(
            db,
            request_key=request_key,
            kind="monthly",
            payload=payload,
            actor_id=int(request.session["admin_id"]),
            reason=f"{month} 월간 요청서 확정",
            details={
                "month": month,
                "changes": [
                    {
                        "point_no": c.point_no,
                        "before": before.get(c.point_no),
                        "action": c.action,
                        "amount": c.amount,
                        "carry_balance": carries[c.point_no],
                    }
                    for c in result.analysis.changes
                ],
            },
            result_url=f"/monthly?done=1&operation={request_key}",
        )
        apply_analysis(db, result.analysis)
        db.flush()
        people = {p.point_no: p for p in db.scalars(select(Person)).all()}
        carry_map = {people[c.point_no].id: carries[c.point_no] for c in result.analysis.changes}
        amount_map = {people[c.point_no].id: c.amount for c in result.analysis.changes}
        records = build_balance_records(db, month, carry_map, amount_map)
        by_id = {p.id: p for p in people.values()}
        notes = {row.point_no: row.note for row in result.rows}
        for record in records:
            person = by_id[record.person_id]
            record.note = notes.get(person.point_no, "")
            person.version += 1
            person.current_carry_balance, person.current_amount = (
                record.carry_balance,
                record.amount,
            )
        create_monthly_snapshot(db, month, records, commit=False, operation_id=operation.id)
        confirm_draft(draft, payload_from_form(form), operation.result_url)
        db.commit()
    except DraftError as exc:
        db.rollback()
        return response(str(exc), exc.status)
    except LedgerConflict as exc:
        db.rollback()
        return response(str(exc), 409)
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
    return RedirectResponse(operation.result_url, status_code=303)


@router.get("/template.xlsx")
def download_template(month: str, request: Request) -> Response:
    try:
        data = request_template(month)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(400, str(exc)) from exc
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="pointbook-request-{month}-v{FORM_VERSION}.xlsx"',
            "Cache-Control": "no-store",
        },
    )
