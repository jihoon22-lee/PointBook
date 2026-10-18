"""R04/R16: 이후 월의 실제 관측이 있으면 더 이전 월을 일반 확정하지 않는다."""

import uuid

import pytest
from sqlalchemy import func, select

from app.models import MonthlySnapshot, Person
from app.services.integrity import inspect_ledger
from app.services.ledger import adjustment_plan, commit_plan, start_write
from tests.factories import make_person
from tests.monthly_helpers import review_fields
from tests.test_profiles import _apply, _new_form

LATER = "2026-09"
EARLIER = "2026-08"


def _register_in_later_month(auth_client, db, monkeypatch):
    from app.routers import people as people_router
    from app.services import profiles

    monkeypatch.setattr(people_router, "current_month", lambda: LATER)
    monkeypatch.setattr(profiles, "current_month", lambda: LATER)
    values = _new_form(
        auth_client,
        point_no="00000101",
        personal_no="101",
        name="합성나중등록",
        carry_balance="500",
        amount="0",
    )
    preview = auth_client.post("/people/new", data=values)
    assert _apply(auth_client, "/people/new", values, preview).status_code == 303
    return db.scalar(select(Person).where(Person.point_no == "00000101"))


def _request(month):
    return {
        "month": month,
        "point_no_0": "00000102",
        "account_type_0": "person",
        "personal_no_0": "102",
        "name_0": "합성재직",
        "team_0": "",
        "grade_0": "소방위",
        "amount_0": "100",
        "carry_0": "0",
    }


def _confirm(auth_client, response):
    values = review_fields(response)
    values.update(deactivated_carry_00000101="0", ack_warnings="yes")
    return auth_client.post("/monthly/confirm", data=values, follow_redirects=False)


def _assert_untouched(db, person):
    db.expire_all()
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0
    assert person.status == "active"
    assert (person.current_carry_balance, person.current_amount) == (500, 0)
    assert inspect_ledger(db).healthy


def test_registration_in_later_month_blocks_earlier_general_confirm(auth_client, db, monkeypatch):
    person = _register_in_later_month(auth_client, db, monkeypatch)
    make_person(db, "102", "합성재직")
    reviewed = auth_client.post("/monthly/review", data=_request(EARLIER))
    assert reviewed.status_code == 400
    assert "합성나중등록(0000 0101, 2026-09)" in reviewed.text
    assert "처리 월" in reviewed.text
    assert review_fields(reviewed)["review_token"] == ""
    assert _confirm(auth_client, reviewed).status_code == 400
    _assert_untouched(db, person)


def test_same_month_as_later_observation_is_still_allowed(auth_client, db, monkeypatch):
    _register_in_later_month(auth_client, db, monkeypatch)
    make_person(db, "102", "합성재직")
    reviewed = auth_client.post("/monthly/review", data=_request(LATER))
    assert review_fields(reviewed)["review_token"]
    assert _confirm(auth_client, reviewed).status_code == 303


def test_current_adjustment_in_later_month_blocks_present_account_too(auth_client, db, monkeypatch):
    from app.services import ledger

    monkeypatch.setattr(ledger, "current_month", lambda: LATER)
    person = make_person(db, "102", "합성재직")
    plan = adjustment_plan(
        db,
        person_id=person.id,
        total="300",
        note="",
        reason="합성 현재 보정",
        request_key=uuid.uuid4().hex,
    )
    start_write(db)
    commit_plan(db, plan, plan.token, 1)
    db.commit()
    reviewed = auth_client.post("/monthly/review", data=_request(EARLIER))
    assert reviewed.status_code == 400
    assert "합성재직(0000 0102, 2026-09)" in reviewed.text
    db.expire_all()
    assert (person.current_carry_balance, person.current_amount) == (300, 0)


def test_confirm_refuses_to_write_behind_a_later_observation(auth_client, db, monkeypatch):
    from app.services import review

    person = _register_in_later_month(auth_client, db, monkeypatch)
    make_person(db, "102", "합성재직")
    monkeypatch.setattr(review, "new_month_error", lambda _db, _month: None)
    reviewed = auth_client.post("/monthly/review", data=_request(EARLIER))
    assert review_fields(reviewed)["review_token"]
    confirmed = _confirm(auth_client, reviewed)
    assert confirmed.status_code == 409
    assert "이후 월" in confirmed.text
    _assert_untouched(db, person)


@pytest.mark.parametrize("count", [6])
def test_block_message_lists_at_most_five_accounts(client, db, count, monkeypatch):
    from app.services import ledger
    from app.services.review import new_month_error

    monkeypatch.setattr(ledger, "current_month", lambda: LATER)
    for index in range(count):
        person = make_person(db, f"20{index}", f"합성보정{index}")
        plan = adjustment_plan(
            db,
            person_id=person.id,
            total="1",
            note="",
            reason="합성 보정",
            request_key=uuid.uuid4().hex,
        )
        start_write(db)
        commit_plan(db, plan, plan.token, 1)
        db.commit()
    message = new_month_error(db, EARLIER)
    assert message is not None and "외 1명" in message
    assert message.count("2026-09)") == 5
