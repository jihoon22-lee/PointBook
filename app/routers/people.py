from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import require_login
from app.db import get_db
from app.models import Person, Team
from app.services.balance import last_record_for_person, previous_total, recompute_record
from app.services.parsing import _to_int
from app.template_utils import render

router = APIRouter(prefix="/people", dependencies=[Depends(require_login)], tags=["people"])


def _load_teams(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.name)).all())


def _person_by_key(db: Session, personal_no: str, name: str) -> Person | None:
    return db.scalar(select(Person).where(Person.personal_no == personal_no, Person.name == name))


def _parse_optional_int(value: str) -> int | None:
    """빈 문자열/숫자가 아닌 값은 None으로 처리 (폼의 '전체/없음' 옵션 대응)."""
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@router.get("")
def list_people(
    request: Request,
    status: str = "",
    team_id: str = "",
    q: str = "",
    db: Session = Depends(get_db),
) -> Response:
    team_id_int = _parse_optional_int(team_id)
    stmt = select(Person)
    if status in ("active", "inactive"):
        stmt = stmt.where(Person.status == status)
    if team_id_int is not None:
        stmt = stmt.where(Person.team_id == team_id_int)
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Person.name.like(pattern), Person.personal_no.like(pattern)))
    persons = list(
        db.scalars(stmt.order_by(Person.status.desc(), Person.team_id, Person.name)).all()
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
        },
    )


@router.get("/new")
def new_person(request: Request, db: Session = Depends(get_db)) -> Response:
    return render(request, "person_form.html", {"teams": _load_teams(db)})


@router.post("/new")
def create_person(
    request: Request,
    personal_no: str = Form(""),
    name: str = Form(""),
    grade: str = Form(""),
    team_id: str = Form(""),
    status: str = Form("active"),
    carry_balance: str = Form("0"),
    amount: str = Form("0"),
    db: Session = Depends(get_db),
) -> Response:
    personal_no = personal_no.strip()
    name = name.strip()
    team_id_int = _parse_optional_int(team_id)
    if not personal_no or not name:
        return render(
            request,
            "person_form.html",
            {"teams": _load_teams(db), "error": "개인번호와 이름은 필수입니다."},
            400,
        )
    if _person_by_key(db, personal_no, name) is not None:
        return render(
            request,
            "person_form.html",
            {
                "teams": _load_teams(db),
                "error": f"'{name}' ({personal_no}) 은(는) 이미 등록된 인원입니다.",
            },
            400,
        )
    person = Person(
        personal_no=personal_no,
        name=name,
        grade=grade.strip(),
        team_id=team_id_int,
        status=status if status in ("active", "inactive") else "active",
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
    personal_no: str = Form(""),
    name: str = Form(""),
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
    team_id_int = _parse_optional_int(team_id)
    if not personal_no or not name:
        return render(
            request,
            "person_form.html",
            {
                "person": person,
                "teams": _load_teams(db),
                "error": "개인번호와 이름은 필수입니다.",
            },
            400,
        )
    duplicate = _person_by_key(db, personal_no, name)
    if duplicate is not None and duplicate.id != person.id:
        return render(
            request,
            "person_form.html",
            {
                "person": person,
                "teams": _load_teams(db),
                "error": f"'{name}' ({personal_no}) 은(는) 이미 등록된 인원입니다.",
            },
            400,
        )
    person.personal_no = personal_no
    person.name = name
    person.grade = grade.strip()
    person.team_id = team_id_int
    person.status = status if status in ("active", "inactive") else "active"
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
        recompute_record(record, previous_total(db, person.id, record.snapshot.month))
    db.commit()
    return RedirectResponse(f"/people/{person.id}", status_code=303)
