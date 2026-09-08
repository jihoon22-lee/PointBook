import json
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import csrf_token, require_login
from app.config import get_settings
from app.db import get_db
from app.models import MonthlyDraft, utcnow
from app.services.drafts import (
    DraftError,
    delete_draft,
    draft_state,
    get_draft,
    payload_from_form,
    save_draft,
)
from app.services.parsing import MAX_REQUEST_ROWS, RawRequestRow
from app.services.review import review_rows
from app.template_utils import render

router = APIRouter(prefix="/drafts", dependencies=[Depends(require_login)], tags=["drafts"])


@router.get("")
def draft_list(request: Request, db: Session = Depends(get_db)) -> Response:
    entries = list(
        db.scalars(
            select(MonthlyDraft)
            .where(
                MonthlyDraft.owner_id == int(request.session["admin_id"]),
                MonthlyDraft.status == "active",
                MonthlyDraft.expires_at > utcnow(),
            )
            .order_by(MonthlyDraft.updated_at.desc())
        )
    )
    return render(
        request,
        "drafts.html",
        {
            "entries": entries,
            "keep_days": get_settings().draft_keep_days,
            "max_active": get_settings().draft_max_active,
        },
    )


@router.get("/session")
def draft_session(request: Request) -> Response:
    return JSONResponse({"csrf_token": csrf_token(request)}, headers={"Cache-Control": "no-store"})


@router.post("/save")
async def draft_save(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form(max_files=0, max_fields=MAX_REQUEST_ROWS * 16 + 50)
    try:
        payload = payload_from_form(form)
        fork = form.get("fork") == "yes"
        draft = save_draft(
            db,
            owner_id=int(request.session["admin_id"]),
            payload=payload,
            request_key=uuid.uuid4().hex if fork else str(form.get("request_key", "")),
            draft_id="" if fork else str(form.get("draft_id", "")),
            version="" if fork else str(form.get("draft_version", "")),
        )
        return JSONResponse(draft_state(draft), headers={"Cache-Control": "no-store"})
    except DraftError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:  # noqa: BLE001
        db.rollback()
        return JSONResponse(
            {"error": "초안을 저장하지 못했습니다. 입력은 이 화면에 남아 있습니다."},
            status_code=500,
        )


@router.get("/{draft_id}")
def draft_open(draft_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    from app.routers.monthly import review_response

    try:
        draft = get_draft(db, draft_id, int(request.session["admin_id"]))
        if draft.status == "confirmed":
            return RedirectResponse(draft.result_url, status_code=303)
        payload = json.loads(draft.payload_json)
        rows = [RawRequestRow(**row) for row in payload["rows"]]
        review = review_rows(
            db, payload["month"], rows, payload["expected_count"], payload["expected_amount"]
        )
        return review_response(
            request,
            payload["month"],
            review,
            request_key=draft.request_key,
            draft_info=draft_state(draft),
            input_source=payload["source"],
            deactivated=payload["deactivated"],
            expected_count=payload["expected_count"],
            expected_amount=payload["expected_amount"],
            message="저장된 입력을 복구했습니다. 현재 기준의 변경 예상을 확인한 뒤 확정하세요.",
        )
    except DraftError as exc:
        return render(
            request,
            "drafts.html",
            {
                "entries": [],
                "error": str(exc),
                "keep_days": get_settings().draft_keep_days,
                "max_active": get_settings().draft_max_active,
            },
            exc.status,
        )


@router.post("/{draft_id}/delete")
async def draft_delete(draft_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    try:
        if form.get("confirm_delete") != "yes":
            raise DraftError("초안 삭제 확인이 필요합니다.")
        delete_draft(
            db,
            draft_id=draft_id,
            owner_id=int(request.session["admin_id"]),
            version=str(form.get("draft_version", "")),
        )
        return RedirectResponse("/drafts", status_code=303)
    except DraftError as exc:
        return render(
            request,
            "drafts.html",
            {
                "entries": [],
                "error": str(exc),
                "keep_days": get_settings().draft_keep_days,
                "max_active": get_settings().draft_max_active,
            },
            exc.status,
        )
