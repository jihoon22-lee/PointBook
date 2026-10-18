import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import FormData

from app.auth import require_login
from app.db import get_db
from app.models import AdminUser, LedgerOperation, Person
from app.services.dates import current_month
from app.services.identifiers import format_point_no
from app.services.integrity import apply_repair, inspect_ledger, person_labels, repair_plan
from app.services.ledger import (
    LedgerConflict,
    LedgerPlan,
    _reason,
    adjustment_plan,
    commit_plan,
    correction_plan,
    find_replay,
    start_write,
)
from app.services.observations import latest_observations
from app.template_utils import kst_datetime, render

router = APIRouter(prefix="/ledger", dependencies=[Depends(require_login)], tags=["ledger"])


def _fields(form: FormData) -> dict[str, str]:
    keys = (
        "month",
        "carry",
        "amount",
        "total",
        "note",
        "reason",
        "request_key",
        "plan_token",
    )
    return {key: str(form.get(key, "")) for key in keys}


def _build(db: Session, kind: str, person_id: int, values: dict[str, str]) -> LedgerPlan:
    common: dict[str, Any] = {
        "person_id": person_id,
        "note": values["note"],
        "reason": values["reason"],
        "request_key": values["request_key"],
    }
    if kind == "correction":
        return correction_plan(
            db,
            **common,
            month=values["month"],
            carry=values["carry"],
            amount=values["amount"],
        )
    return adjustment_plan(db, **common, total=values["total"])


def _person(db: Session, person_id: int) -> Person | None:
    return db.scalar(select(Person).options(joinedload(Person.team)).where(Person.id == person_id))


def _form(
    request: Request,
    db: Session,
    kind: str,
    person_id: int,
    values: dict[str, str],
    *,
    plan: LedgerPlan | None = None,
    error: str = "",
    status: int = 200,
) -> Response:
    person = _person(db, person_id)
    if person is None:
        return render(
            request,
            "ledger_form.html",
            {
                "error": "대상 인원을 찾을 수 없습니다.",
                "person": None,
                "values": values,
                "kind": kind,
            },
            404,
        )
    return render(
        request,
        "ledger_form.html",
        {
            "person": person,
            "kind": kind,
            "values": values,
            "plan": plan,
            "error": error,
            "observation": latest_observations(db, person_ids=[person_id]).get(person_id),
        },
        status,
    )


@router.get("")
def operations(
    request: Request, page: int = Query(default=1, ge=1, le=1000000), db: Session = Depends(get_db)
) -> Response:
    entries = list(
        db.scalars(
            select(LedgerOperation)
            .order_by(LedgerOperation.id.desc())
            .offset((page - 1) * 100)
            .limit(100)
        )
    )
    return render(
        request,
        "ledger_operations.html",
        {
            "entries": entries,
            "page": page,
            "total": db.scalar(select(func.count(LedgerOperation.id))) or 0,
        },
    )


MONTHLY_ACTION_LABELS = [
    ("new", "신규"),
    ("returned", "복귀"),
    ("kept", "재직 유지"),
    ("deactivated", "비재직 전환"),
    ("inactive_kept", "기존 비재직 잔액 보존"),
]


def _monthly_summary(db: Session, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """월간 확정의 처리 유형별 인원. 이름은 다른 화면처럼 현재 인원 정보로 보인다."""
    points = {str(change.get("point_no", "")) for change in changes}
    names: dict[str, str] = {
        point_no: name
        for point_no, name in db.execute(
            select(Person.point_no, Person.name).where(Person.point_no.in_(points))
        )
    }
    summary = []
    for action, label in MONTHLY_ACTION_LABELS:
        members = [
            names.get(change["point_no"]) or format_point_no(change["point_no"])
            for change in changes
            if change.get("action") == action
        ]
        if members:
            summary.append({"label": label, "names": members})
    return summary


def _applied(url: str) -> Response:
    """반영 직후 이동한 결과 화면에서만 반영 완료를 알린다. 저장된 결과 주소는 바꾸지 않는다."""
    return RedirectResponse(f"{url}?done=1", status_code=303)


@router.get("/operations/{key}")
def operation_detail(
    key: str, request: Request, done: str = "", db: Session = Depends(get_db)
) -> Response:
    operation = db.scalar(select(LedgerOperation).where(LedgerOperation.request_key == key))
    if operation is None:
        return RedirectResponse("/ledger", status_code=303)
    actor = db.get(AdminUser, operation.actor_id) if operation.actor_id else None
    details = json.loads(operation.detail_json)
    summary = (
        _monthly_summary(db, details.get("changes", [])) if operation.kind == "monthly" else []
    )
    return render(
        request,
        "ledger_operation.html",
        {
            "operation": operation,
            "details": details,
            "actor": actor.username if actor else "작성자 미확인",
            "created_at": kst_datetime(operation.created_at),
            "done": done == "1",
            "monthly_summary": summary,
        },
    )


@router.get("/correct/{person_id}")
def correction_form(
    person_id: int, request: Request, month: str = "", db: Session = Depends(get_db)
) -> Response:
    person = _person(db, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    records = sorted(person.balances, key=lambda r: r.snapshot.month, reverse=True)
    month = month or (records[0].snapshot.month if records else current_month())
    record = next((r for r in records if r.snapshot.month == month), None)
    values = {
        "month": month,
        "carry": str(record.carry_balance) if record else "",
        "amount": str(record.amount) if record else "",
        "total": "",
        "note": record.note if record else "",
        "reason": "",
        "request_key": uuid.uuid4().hex,
        "plan_token": "",
    }
    return _form(request, db, "correction", person_id, values)


@router.get("/adjust/{person_id}")
def adjustment_form(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    person = _person(db, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    values = {
        "month": current_month(),
        "carry": "",
        "amount": "",
        "total": str(person.current_carry_balance + person.current_amount),
        "note": "",
        "reason": "",
        "request_key": uuid.uuid4().hex,
        "plan_token": "",
    }
    return _form(request, db, "adjustment", person_id, values)


async def _preview(request: Request, db: Session, kind: str, person_id: int) -> Response:
    values = _fields(await request.form())
    try:
        plan = _build(db, kind, person_id, values)
        return _form(request, db, kind, person_id, values, plan=plan)
    except ValueError as exc:
        return _form(request, db, kind, person_id, values, error=str(exc), status=400)


async def _apply(request: Request, db: Session, kind: str, person_id: int) -> Response:
    values = _fields(await request.form())
    try:
        candidate = _build(db, kind, person_id, values)
        replay = find_replay(db, values["request_key"], kind, candidate.payload)
        if replay:
            return _applied(replay.result_url)
        start_write(db)
        replay = find_replay(db, values["request_key"], kind, candidate.payload)
        if replay:
            db.rollback()
            return _applied(replay.result_url)
        plan = _build(db, kind, person_id, values)
        operation = commit_plan(db, plan, values["plan_token"], int(request.session["admin_id"]))
        db.commit()
        return _applied(operation.result_url)
    except (LedgerConflict, IntegrityError, OperationalError):
        db.rollback()
        return _form(
            request,
            db,
            kind,
            person_id,
            values,
            error="입력 또는 기준이 변경되었습니다. 전후 차이를 다시 검토하세요.",
            status=409,
        )
    except ValueError as exc:
        db.rollback()
        return _form(request, db, kind, person_id, values, error=str(exc), status=400)
    except Exception:  # noqa: BLE001 - 금융/개인정보 SQL 원문을 노출하지 않는다.
        db.rollback()
        return _form(
            request,
            db,
            kind,
            person_id,
            values,
            error="정정 처리에 실패해 변경을 롤백했습니다. 입력은 보존했습니다.",
            status=500,
        )


@router.post("/correct/{person_id}/preview")
async def correction_preview(
    person_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    return await _preview(request, db, "correction", person_id)


@router.post("/correct/{person_id}/apply")
async def correction_apply(
    person_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    return await _apply(request, db, "correction", person_id)


@router.post("/adjust/{person_id}/preview")
async def adjustment_preview(
    person_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    return await _preview(request, db, "adjustment", person_id)


@router.post("/adjust/{person_id}/apply")
async def adjustment_apply(
    person_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    return await _apply(request, db, "adjustment", person_id)


def _integrity_page(
    request: Request,
    db: Session,
    *,
    values: dict[str, str] | None = None,
    plan: LedgerPlan | None = None,
    error: str = "",
    status: int = 200,
) -> Response:
    return render(
        request,
        "ledger_integrity.html",
        {
            "report": inspect_ledger(db),
            "plan": plan,
            "person_labels": person_labels(
                db,
                {change["person_id"] for change in plan.changes}
                | {current["person_id"] for current in plan.current_changes},
            )
            if plan
            else {},
            "values": values or {"request_key": uuid.uuid4().hex, "reason": ""},
            "error": error,
        },
        status,
    )


@router.get("/integrity")
def integrity(request: Request, db: Session = Depends(get_db)) -> Response:
    return _integrity_page(request, db)


@router.post("/repair/preview")
async def repair_preview(request: Request, db: Session = Depends(get_db)) -> Response:
    values = _fields(await request.form())
    try:
        plan = repair_plan(db, reason=values["reason"], request_key=values["request_key"])
        return _integrity_page(request, db, values=values, plan=plan)
    except ValueError as exc:
        return _integrity_page(request, db, values=values, error=str(exc), status=400)


@router.post("/repair/apply")
async def repair_apply(request: Request, db: Session = Depends(get_db)) -> Response:
    values = _fields(await request.form())
    try:
        payload: dict[str, Any] = {
            "reason": _reason(values["reason"]),
            "scope": "calculated_fields",
        }
        replay = find_replay(db, values["request_key"], "repair", payload)
        if replay:
            return _applied(replay.result_url)
        start_write(db)
        replay = find_replay(db, values["request_key"], "repair", payload)
        if replay:
            db.rollback()
            return _applied(replay.result_url)
        plan = repair_plan(db, reason=values["reason"], request_key=values["request_key"])
        url = apply_repair(db, plan, values["plan_token"], int(request.session["admin_id"]))
        db.commit()
        return _applied(url)
    except (LedgerConflict, IntegrityError, OperationalError):
        db.rollback()
        return _integrity_page(
            request,
            db,
            values=values,
            error="기준이 변경되었습니다. 복구 계획을 다시 검토하세요.",
            status=409,
        )
    except ValueError as exc:
        db.rollback()
        return _integrity_page(request, db, values=values, error=str(exc), status=400)
    except Exception:  # noqa: BLE001
        db.rollback()
        return _integrity_page(
            request,
            db,
            values=values,
            error="복구 실패로 변경을 롤백했습니다. 보존 사본과 상태를 확인하세요.",
            status=500,
        )
