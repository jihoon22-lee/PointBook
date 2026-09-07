import json
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.auth import require_login
from app.db import get_db
from app.models import BalanceAdjustment, BalanceRecord, Person, Team
from app.services.backup import BackupError
from app.services.dates import current_month
from app.services.history import profile_for_record
from app.services.ledger import LedgerConflict, find_replay, ledger_version, start_write
from app.services.observations import latest_observations
from app.services.profiles import (
    PROFILE_FIELDS,
    apply_profile,
    identity_changed,
    prepare_profile,
    profile_payload,
)
from app.template_utils import render

router = APIRouter(prefix="/people", dependencies=[Depends(require_login)], tags=["people"])

PAGE_SIZE = 50
PEOPLE_SORT_KEYS = frozenset(
    {"name", "point_no", "personal_no", "account_type", "team", "grade", "status", "total"}
)


def _load_teams(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.name)).all())


def _parse_optional_int(value: str) -> int | None:
    try:
        return int(value) if value.strip() else None
    except ValueError:
        return None


async def _person_form_values(request: Request) -> dict[str, str]:
    form = await request.form()
    values = {
        field: str(form.get(field, ""))
        for field in (
            *PROFILE_FIELDS,
            "reason",
            "request_key",
            "base_version",
            "person_version",
            "initial_month",
            "plan_token",
            "intent",
            "confirm_identity",
        )
    }
    for field in ("carry_balance", "amount"):
        if field in form:
            values[field] = str(form[field])
    return values


def _form_context(
    db: Session, person: Person | None = None, values: dict[str, str] | None = None
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "person": person,
        "teams": _load_teams(db),
        "preview": None,
        "conflict": False,
    }
    if values is None:
        values = {
            "request_key": secrets.token_urlsafe(24),
            "base_version": str(ledger_version(db)),
            "person_version": str(person.version) if person else "",
            "initial_month": current_month(),
            "reason": "" if person else "신규 계정 등록",
            "carry_balance": "0",
            "amount": "0",
        }
        values.update(
            {field: str(getattr(person, field) or "") if person else "" for field in PROFILE_FIELDS}
        )
        if person is None:
            values.update(account_type="person", status="active")
    context["values"] = values
    return context


async def _save_profile(request: Request, db: Session, person_id: int | None) -> Response:
    person = db.get(Person, person_id) if person_id is not None else None
    if person_id is not None and person is None:
        return RedirectResponse("/people", status_code=303)
    values = await _person_form_values(request)
    try:
        payload = profile_payload(values, person_id)
        if values["intent"] == "apply":
            start_write(db)
            replay = find_replay(db, values["request_key"], "profile", payload)
            if replay is not None:
                url = replay.result_url
                db.rollback()
                return RedirectResponse(url, status_code=303)
        elif values["intent"] == "rebase":
            db.expire_all()
            person = db.get(Person, person_id) if person_id is not None else None
            values["base_version"] = str(ledger_version(db))
            values["person_version"] = str(person.version) if person else ""
            if person_id is None:
                values["initial_month"] = current_month()
                payload["month"] = values["initial_month"]
        plan = prepare_profile(
            db,
            payload=payload,
            request_key=values["request_key"],
            base_version=values["base_version"],
            person_version=values["person_version"],
        )
        if values["intent"] == "apply":
            operation = apply_profile(
                db,
                plan,
                values["plan_token"],
                int(request.session["admin_id"]),
                identity_confirmed=values["confirm_identity"] == "yes",
            )
            db.commit()
            return RedirectResponse(operation.result_url, status_code=303)
        context = _form_context(db, person, values)
        context.update(preview=plan, identity_changed=identity_changed(plan))
        return render(request, "person_form.html", context)
    except (ValueError, BackupError, SQLAlchemyError, OSError) as exc:
        db.rollback()
        person = db.get(Person, person_id) if person_id is not None else None
        context = _form_context(db, person, values)
        conflict = isinstance(exc, (LedgerConflict, SQLAlchemyError))
        if isinstance(exc, SQLAlchemyError):
            message = "DB 상태가 변경되어 저장할 수 없습니다. 새 기준으로 다시 검토해 주세요."
        elif isinstance(exc, OSError):
            message = "변경 전 백업을 저장할 수 없습니다. 공간·권한을 확인한 뒤 다시 검토해 주세요."
        else:
            message = str(exc)
        context.update(error=message, conflict=conflict)
        return render(request, "person_form.html", context, 409 if conflict else 400)


def _people_order(sort_key: str, direction: str) -> list[Any]:
    total_balance = Person.current_carry_balance + Person.current_amount
    expressions = {
        "name": Person.name,
        "point_no": Person.point_no,
        "personal_no": Person.personal_no,
        "account_type": case((Person.account_type == "person", 0), else_=1),
        "team": Team.name,
        "grade": Person.grade,
        "status": case((Person.status == "active", 0), else_=1),
        "total": total_balance,
    }
    expression = expressions[sort_key]
    order: list[Any] = []
    if sort_key in {"personal_no", "team", "grade"}:
        order.append(case((expression.is_(None), 1), else_=0))
    order.append(expression.desc() if direction == "desc" else expression.asc())
    order.extend((Person.name.asc(), Person.id.asc()))
    return order


@router.get("")
def list_people(
    request: Request,
    status: str = "",
    team_id: str = "",
    q: str = "",
    sort: str = "status",
    direction: str = Query("asc", alias="dir"),
    page: int = 1,
    db: Session = Depends(get_db),
) -> Response:
    sort = sort if sort in PEOPLE_SORT_KEYS else "status"
    direction = direction if direction in {"asc", "desc"} else "asc"
    team_id_int = _parse_optional_int(team_id)
    filters = []
    if status in ("active", "inactive"):
        filters.append(Person.status == status)
    if team_id_int is not None:
        filters.append(Person.team_id == team_id_int)
    if q.strip():
        pattern = f"%{q.strip()}%"
        compact_point_no = re.sub(r"[\s-]+", "", q.strip())
        filters.append(
            or_(
                Person.name.like(pattern),
                Person.personal_no.like(pattern),
                Person.point_no.like(f"%{compact_point_no}%"),
            )
        )

    stmt = select(Person).outerjoin(Team).options(selectinload(Person.team))
    if filters:
        stmt = stmt.where(*filters)
    total = (
        db.scalar(
            select(func.count(Person.id)).where(*filters)
            if filters
            else select(func.count(Person.id))
        )
        or 0
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    persons = list(
        db.scalars(
            stmt.order_by(*_people_order(sort, direction))
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).all()
    )
    return render(
        request,
        "people.html",
        {
            "persons": persons,
            "teams": _load_teams(db),
            "status": status,
            "team_id": team_id_int,
            "q": q,
            "sort": sort,
            "direction": direction,
            "page": page,
            "pages": pages,
            "total": total,
        },
    )


@router.get("/new")
def new_person(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(request, "person_form.html", _form_context(db))


@router.post("/new")
async def create_person(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _save_profile(request, db, None)


@router.get("/{person_id}")
def person_detail(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    person = db.scalar(
        select(Person).options(joinedload(Person.team)).where(Person.id == person_id)
    )
    if person is None:
        return RedirectResponse("/people", status_code=303)
    records = list(
        db.scalars(
            select(BalanceRecord)
            .options(joinedload(BalanceRecord.snapshot))
            .where(BalanceRecord.person_id == person_id)
        )
    )
    records.sort(key=lambda record: record.snapshot.month, reverse=True)
    adjustments = list(
        db.scalars(
            select(BalanceAdjustment)
            .where(BalanceAdjustment.person_id == person_id)
            .order_by(BalanceAdjustment.month.desc(), BalanceAdjustment.observed_at.desc())
        )
    )
    adjustment_rows = []
    for adjustment in adjustments:
        try:
            profile = json.loads(adjustment.profile_data)
        except (TypeError, ValueError):
            profile = {}
        adjustment_rows.append(
            {"record": adjustment, "profile": profile if isinstance(profile, dict) else {}}
        )
    return render(
        request,
        "person_detail.html",
        {
            "person": person,
            "history": [
                {"record": record, "profile": profile_for_record(record)} for record in records
            ],
            "adjustments": adjustment_rows,
            "latest": latest_observations(db, person_ids=[person_id]).get(person_id),
        },
    )


@router.get("/{person_id}/edit")
def edit_person_form(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    person = db.get(Person, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    return render(request, "person_form.html", _form_context(db, person))


@router.post("/{person_id}/edit")
async def edit_person(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    return await _save_profile(request, db, person_id)
