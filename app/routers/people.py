import re
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_login
from app.db import get_db
from app.models import Person, Team
from app.services.balance import last_record_for_person, previous_total_or_none, recompute_record
from app.services.identifiers import normalize_point_no
from app.services.parsing import _to_int
from app.template_utils import render

router = APIRouter(prefix="/people", dependencies=[Depends(require_login)], tags=["people"])

PAGE_SIZE = 50
PEOPLE_SORT_KEYS = frozenset(
    {"name", "point_no", "personal_no", "account_type", "team", "grade", "status", "total"}
)


def _load_teams(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.name)).all())


def _person_by_point_no(db: Session, point_no: str) -> Person | None:
    return db.scalar(select(Person).where(Person.point_no == point_no))


def _parse_optional_int(value: str) -> int | None:
    """빈 문자열/숫자가 아닌 값은 None으로 처리 (폼의 '전체/없음' 옵션 대응)."""
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


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
    return render(request, "person_form.html", {"teams": _load_teams(db)})


@router.post("/new")
def create_person(
    request: Request,
    point_no: str = Form(""),
    personal_no: str = Form(""),
    name: str = Form(""),
    account_type: str = Form("person"),
    grade: str = Form(""),
    team_id: str = Form(""),
    status: str = Form("active"),
    carry_balance: str = Form("0"),
    amount: str = Form("0"),
    db: Session = Depends(get_db),
) -> Response:
    personal_no = personal_no.strip()
    name = name.strip()
    account_type = account_type if account_type in ("person", "shared") else "person"
    team_id_int = _parse_optional_int(team_id)
    try:
        normalized_point_no = normalize_point_no(point_no)
    except ValueError as exc:
        return render(
            request,
            "person_form.html",
            {"teams": _load_teams(db), "error": str(exc)},
            400,
        )
    if not name or (account_type == "person" and not personal_no):
        return render(
            request,
            "person_form.html",
            {
                "teams": _load_teams(db),
                "error": "포인트번호와 이름은 필수입니다. 일반 인원은 개인번호도 필요합니다.",
            },
            400,
        )
    if _person_by_point_no(db, normalized_point_no) is not None:
        return render(
            request,
            "person_form.html",
            {
                "teams": _load_teams(db),
                "error": f"포인트번호 {normalized_point_no} 은(는) 이미 등록된 인원입니다.",
            },
            400,
        )
    person = Person(
        point_no=normalized_point_no,
        personal_no=personal_no or None,
        name=name,
        grade=grade.strip() if account_type == "person" else "",
        team_id=team_id_int,
        status=(status if status in ("active", "inactive") else "active")
        if account_type == "person"
        else "active",
        account_type=account_type,
        current_carry_balance=_to_int(carry_balance),
        current_amount=_to_int(amount),
    )
    db.add(person)
    db.commit()
    return RedirectResponse(f"/people/{person.id}", status_code=303)


@router.get("/{person_id}")
def person_detail(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    person = db.get(Person, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    balances = sorted(person.balances, key=lambda b: b.snapshot.month, reverse=True)
    return render(request, "person_detail.html", {"person": person, "balances": balances})


@router.get("/{person_id}/edit")
def edit_person_form(person_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    person = db.get(Person, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    return render(request, "person_form.html", {"person": person, "teams": _load_teams(db)})


@router.post("/{person_id}/edit")
def edit_person(
    person_id: int,
    request: Request,
    point_no: str = Form(""),
    personal_no: str = Form(""),
    name: str = Form(""),
    account_type: str = Form("person"),
    grade: str = Form(""),
    team_id: str = Form(""),
    status: str = Form("active"),
    carry_balance: str = Form("0"),
    amount: str = Form("0"),
    db: Session = Depends(get_db),
) -> Response:
    person = db.get(Person, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    personal_no = personal_no.strip()
    name = name.strip()
    account_type = account_type if account_type in ("person", "shared") else "person"
    team_id_int = _parse_optional_int(team_id)
    try:
        normalized_point_no = normalize_point_no(point_no)
    except ValueError as exc:
        return render(
            request,
            "person_form.html",
            {
                "person": person,
                "teams": _load_teams(db),
                "error": str(exc),
            },
            400,
        )
    if not name or (account_type == "person" and not personal_no):
        return render(
            request,
            "person_form.html",
            {
                "person": person,
                "teams": _load_teams(db),
                "error": "포인트번호와 이름은 필수입니다. 일반 인원은 개인번호도 필요합니다.",
            },
            400,
        )
    duplicate = _person_by_point_no(db, normalized_point_no)
    if duplicate is not None and duplicate.id != person.id:
        return render(
            request,
            "person_form.html",
            {
                "person": person,
                "teams": _load_teams(db),
                "error": f"포인트번호 {normalized_point_no} 은(는) 이미 등록된 인원입니다.",
            },
            400,
        )
    person.point_no = normalized_point_no
    person.personal_no = personal_no or None
    person.name = name
    person.grade = grade.strip() if account_type == "person" else ""
    person.team_id = team_id_int
    person.status = (
        (status if status in ("active", "inactive") else "active")
        if account_type == "person"
        else "active"
    )
    person.account_type = account_type
    carry = _to_int(carry_balance)
    amt = _to_int(amount)
    person.current_carry_balance = carry
    person.current_amount = amt
    # 개별 수정은 항상 "현재 상태 + 가장 최근 월 기록 1건"만 변경한다.
    # 이전 월 기록은 건드리지 않으며(역사 보존), 이후 월은 아직 존재하지 않으므로
    # 최신 기록만 직전 총 잔액 기준으로 재계산하면 정합성이 유지된다.
    record = last_record_for_person(db, person)
    if record is not None:
        record.carry_balance = carry
        record.amount = amt
        recompute_record(record, previous_total_or_none(db, person.id, record.snapshot.month))
    db.commit()
    return RedirectResponse(f"/people/{person.id}", status_code=303)
