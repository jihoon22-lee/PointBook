import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_login
from app.db import get_db
from app.models import Person, Team
from app.services.balance import last_record_for_person, previous_total_or_none, recompute_record
from app.services.identifiers import normalize_point_no
from app.services.validation import parse_money
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


async def _person_form_values(request: Request) -> dict[str, str]:
    # Form(default) replaces explicit empty fields with their defaults. Read raw fields
    # here so empty money/type/status values remain errors and survive the response.
    defaults = {
        "point_no": "",
        "personal_no": "",
        "name": "",
        "account_type": "",
        "grade": "",
        "team_id": "",
        "status": "",
        "carry_balance": "",
        "amount": "",
    }
    form = await request.form()
    return {key: str(form.get(key, default)) for key, default in defaults.items()}


def _validate_person_values(
    db: Session,
    values: dict[str, str],
    person_id: int | None = None,
) -> tuple[str, int | None, int, int]:
    if values["account_type"] not in {"person", "shared"}:
        raise ValueError("계정 구분은 일반 인원 또는 공용 계정이어야 합니다.")
    if values["status"] not in {"active", "inactive"}:
        raise ValueError("상태는 재직 또는 비재직이어야 합니다.")
    if values["account_type"] == "shared" and values["status"] != "active":
        raise ValueError("공용 계정은 항상 재직 상태여야 합니다.")
    point_no = normalize_point_no(values["point_no"])
    if not values["name"].strip() or (
        values["account_type"] == "person" and not values["personal_no"].strip()
    ):
        raise ValueError("포인트번호와 이름은 필수입니다. 일반 인원은 개인번호도 필요합니다.")
    team_id = None
    if values["team_id"] != "":
        raw_team = values["team_id"]
        if not re.fullmatch(r"[0-9]{1,18}", raw_team) or int(raw_team) < 1:
            raise ValueError("올바른 팀을 선택해 주세요.")
        team_id = int(raw_team)
        if db.get(Team, team_id) is None:
            raise ValueError("선택한 팀이 존재하지 않습니다. 팀을 다시 선택해 주세요.")
    carry = parse_money(values["carry_balance"], label="이월 잔액")
    amount = parse_money(values["amount"], label="당월 충전 금액")
    duplicate = _person_by_point_no(db, point_no)
    if duplicate is not None and duplicate.id != person_id:
        raise ValueError(f"포인트번호 {point_no} 은(는) 이미 등록된 인원입니다.")
    return point_no, team_id, carry, amount


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
async def create_person(request: Request, db: Session = Depends(get_db)) -> Response:
    values = await _person_form_values(request)
    try:
        point_no, team_id, carry, amount = _validate_person_values(db, values)
    except ValueError as exc:
        return render(
            request,
            "person_form.html",
            {
                "teams": _load_teams(db),
                "values": values,
                "error": str(exc),
            },
            400,
        )
    person = Person(
        point_no=point_no,
        personal_no=values["personal_no"].strip() or None,
        name=values["name"].strip(),
        grade=values["grade"].strip() if values["account_type"] == "person" else "",
        team_id=team_id,
        status=values["status"],
        account_type=values["account_type"],
        current_carry_balance=carry,
        current_amount=amount,
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
async def edit_person(
    person_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    person = db.get(Person, person_id)
    if person is None:
        return RedirectResponse("/people", status_code=303)
    values = await _person_form_values(request)
    try:
        point_no, team_id, carry, amount = _validate_person_values(db, values, person.id)
    except ValueError as exc:
        return render(
            request,
            "person_form.html",
            {
                "person": person,
                "teams": _load_teams(db),
                "values": values,
                "error": str(exc),
            },
            400,
        )
    person.point_no = point_no
    person.personal_no = values["personal_no"].strip() or None
    person.name = values["name"].strip()
    person.grade = values["grade"].strip() if values["account_type"] == "person" else ""
    person.team_id = team_id
    person.status = values["status"]
    person.account_type = values["account_type"]
    person.current_carry_balance = carry
    person.current_amount = amount
    # WP07의 전용 정정 경로 도입 전까지 기존 최신 월 금액 수정 동작을 유지한다.
    record = last_record_for_person(db, person)
    if record is not None:
        record.carry_balance = carry
        record.amount = amount
        recompute_record(record, previous_total_or_none(db, person.id, record.snapshot.month))
    db.commit()
    return RedirectResponse(f"/people/{person.id}", status_code=303)
