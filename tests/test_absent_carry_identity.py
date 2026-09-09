"""비재직 입력의 인원 연결·이전 초안·직접 HTTP 위변조 회귀."""

import json
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as db_module
from app.models import BalanceRecord, LedgerOperation, MonthlyDraft, Person
from app.services.absent_carries import preserved_entries, reconcile
from app.services.parsing import RawRequestRow
from app.services.review import review_rows
from tests.factories import make_person
from tests.monthly_helpers import review_fields


def request_data():
    return {
        "month": "2026-08",
        "point_no_0": "00000101",
        "personal_no_0": "101",
        "name_0": "합성재직",
        "account_type_0": "person",
        "grade_0": "",
        "team_0": "",
        "amount_0": "1000",
        "carry_0": "0",
    }


def start_with_absent(client, db):
    person = make_person(db, "999", "합성비재직A", grade="", status="inactive")
    values = review_fields(client.post("/monthly/review", data=request_data()))
    assert values["absent_binding_00000999"]
    return person, values


def save_and_open(client, values):
    saved = client.post("/drafts/save", data=values)
    assert saved.status_code == 200, saved.text
    return review_fields(client.get("/drafts/" + saved.json()["draft_id"]))


def test_reused_number_never_receives_another_person_saved_carry(auth_client, db):
    first, values = start_with_absent(auth_client, db)
    second = make_person(db, "888", "합성비재직B", grade="", status="inactive")
    values = review_fields(auth_client.post("/monthly/review", data=values))
    values.update(deactivated_carry_00000999="123", deactivated_carry_00000888="456")
    values = save_and_open(auth_client, values)
    first.point_no = "00000777"
    first.version += 1
    db.commit()
    second.point_no = "00000999"
    second.version += 1
    db.commit()
    old_values = {**values, "ack_warnings": "yes"}
    assert auth_client.post("/monthly/confirm", data=old_values).status_code == 409
    reopened = review_fields(auth_client.get("/drafts/" + values["draft_id"]))
    assert reopened["deactivated_carry_00000999"] == ""
    assert reopened["deactivated_carry_00000777"] == ""
    kept = preserved_entries(reopened["preserved_absent_carry"])
    assert {(entry["name"], entry["value"]) for entry in kept} == {
        ("합성비재직A", "123"),
        ("합성비재직B", "456"),
    }
    # 새 검수 토큰에 옛 입력·binding을 붙여도 다른 인원으로 결합하지 않는다.
    crossed = {
        **reopened,
        "absent_binding_00000999": values["absent_binding_00000999"],
        "deactivated_carry_00000999": "123",
        "deactivated_carry_00000777": "0",
        "ack_warnings": "yes",
    }
    assert auth_client.post("/monthly/confirm", data=crossed).status_code == 400
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    fresh = review_fields(auth_client.post("/monthly/review", data=reopened))
    assert fresh["deactivated_carry_00000999"] == ""
    fresh.update(
        deactivated_carry_00000999="222", deactivated_carry_00000777="111", ack_warnings="yes"
    )
    confirmed = auth_client.post("/monthly/confirm", data=fresh, follow_redirects=False)
    assert confirmed.status_code == 303
    db.expire_all()
    assert first.current_carry_balance == 111 and second.current_carry_balance == 222
    draft = db.get(MonthlyDraft, fresh["draft_id"])
    assert len(preserved_entries(json.loads(draft.payload_json)["preserved_absent_carries"])) == 2
    replay = auth_client.post("/monthly/confirm", data=fresh, follow_redirects=False)
    assert replay.status_code == 303 and replay.headers["location"] == confirmed.headers["location"]
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1


@pytest.mark.parametrize("binding", ["", "tampered"])
def test_unsigned_or_tampered_binding_cannot_confirm(auth_client, db, binding):
    _person, values = start_with_absent(auth_client, db)
    values.update(
        absent_binding_00000999=binding, deactivated_carry_00000999="123", ack_warnings="yes"
    )
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 400
    assert "다시 입력" in response.text
    recovered = review_fields(response)
    assert recovered["deactivated_carry_00000999"] == ""
    assert preserved_entries(recovered["preserved_absent_carry"])[0]["value"] == "123"
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_legacy_draft_is_preserved_but_requires_explicit_reentry(auth_client, db):
    _person, values = start_with_absent(auth_client, db)
    draft = db.get(MonthlyDraft, values["draft_id"])
    payload = json.loads(draft.payload_json)
    payload.pop("absent_bindings")
    payload.pop("preserved_absent_carries")
    payload["deactivated"] = {"00000999": "12.5"}
    draft.payload_json = json.dumps(payload)
    db.commit()
    values = review_fields(auth_client.get("/drafts/" + draft.id))
    assert values["deactivated_carry_00000999"] == ""
    kept = preserved_entries(values["preserved_absent_carry"])
    assert kept[0]["value"] == "12.5" and kept[0]["name"] == "인원 연결 미확인"
    reviewed = review_fields(auth_client.post("/monthly/review", data=values))
    assert reviewed["deactivated_carry_00000999"] == ""
    reviewed.update(deactivated_carry_00000999="0", ack_warnings="yes")
    assert (
        auth_client.post("/monthly/confirm", data=reviewed, follow_redirects=False).status_code
        == 303
    )


@pytest.mark.parametrize("changed", ["name", "personal_no", "month", "action"])
def test_changed_identity_month_or_action_requires_reentry(auth_client, db, changed):
    person, values = start_with_absent(auth_client, db)
    values["deactivated_carry_00000999"] = "1,000"
    values = save_and_open(auth_client, values)
    if changed == "month":
        values["month"] = "2026-09"
    elif changed == "action":
        person.status = "active"
    else:
        setattr(person, changed, "합성변경" if changed == "name" else "998")
    person.version += 1
    db.commit()
    reviewed = review_fields(auth_client.post("/monthly/review", data=values))
    assert reviewed["deactivated_carry_00000999"] == ""
    assert preserved_entries(reviewed["preserved_absent_carry"])[0]["value"] == "1,000"


def test_same_identity_survives_profile_version_and_invalid_request(auth_client, db):
    person, values = start_with_absent(auth_client, db)
    values["deactivated_carry_00000999"] = "12.5"
    values = save_and_open(auth_client, values)
    person.grade = "소방위"
    person.version += 1
    db.commit()
    values["amount_0"] = "wrong"
    invalid = auth_client.post("/monthly/review", data=values)
    assert invalid.status_code == 400
    recovering = review_fields(invalid)
    assert recovering["deactivated_carry_00000999"] == "12.5"
    assert recovering["absent_binding_00000999"] == values["absent_binding_00000999"]
    recovering["amount_0"] = "1000"
    recovered = review_fields(auth_client.post("/monthly/review", data=recovering))
    assert recovered["deactivated_carry_00000999"] == "12.5"
    assert "preserved_absent_carry" not in recovered


def test_excluded_balance_is_preserved_without_reactivation(auth_client, db):
    person, values = start_with_absent(auth_client, db)
    values["deactivated_carry_00000999"] = "123"
    values = save_and_open(auth_client, values)
    values.update(
        point_no_1=person.point_no,
        personal_no_1=person.personal_no,
        name_1=person.name,
        grade_1="",
        team_1="",
        account_type_1="person",
        amount_1="1000",
        carry_1="200",
    )
    returned = review_fields(auth_client.post("/monthly/review", data=values))
    assert "deactivated_carry_00000999" not in returned
    assert preserved_entries(returned["preserved_absent_carry"])[0]["value"] == "123"
    removed = {key: value for key, value in returned.items() if not key.endswith("_1")}
    absent = review_fields(auth_client.post("/monthly/review", data=removed))
    assert absent["deactivated_carry_00000999"] == ""
    assert preserved_entries(absent["preserved_absent_carry"])[0]["value"] == "123"


def test_server_preserved_values_survive_further_autosave_from_old_page(auth_client, db):
    person, values = start_with_absent(auth_client, db)
    person.personal_no = "998"
    person.version += 1
    db.commit()
    values["deactivated_carry_00000999"] = "123"
    first = auth_client.post("/drafts/save", data=values)
    assert first.status_code == 200
    # 자동 저장 JSON 응답에는 새 원문보존 토큰이 없으므로 기존 화면은 알 수 없다.
    values["draft_version"] = str(first.json()["draft_version"])
    values["deactivated_carry_00000999"] = "456"
    second = auth_client.post("/drafts/save", data=values)
    assert second.status_code == 200
    reopened = review_fields(auth_client.get("/drafts/" + values["draft_id"]))
    assert reopened["deactivated_carry_00000999"] == ""
    assert {entry["value"] for entry in preserved_entries(reopened["preserved_absent_carry"])} == {
        "123",
        "456",
    }


def test_identity_race_before_writer_lock_blocks_confirmation(auth_client, db, monkeypatch):
    from app.routers import monthly

    person, values = start_with_absent(auth_client, db)
    values.update(deactivated_carry_00000999="123", ack_warnings="yes")
    original = monthly.carry_values
    changed = False

    def concurrent_change(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            with Session(db_module.engine) as other:
                current = other.get(Person, person.id)
                current.name = "합성동시변경"
                current.version += 1
                other.commit()
        return result

    monkeypatch.setattr(monthly, "carry_values", concurrent_change)
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 409
    assert "다른 작업" in response.text
    restored = review_fields(response)
    assert restored["deactivated_carry_00000999"] == ""
    assert preserved_entries(restored["preserved_absent_carry"])[0]["value"] == "123"
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_large_multipart_draft_accepts_bindings_and_preserved_inputs(auth_client, db):
    people = [
        Person(
            point_no=f"{i:08d}",
            personal_no=str(i),
            name=f"합성{i}",
            grade="",
            account_type="person",
            status="active" if i <= 2000 else "inactive",
        )
        for i in range(1, 4001)
    ]
    db.add_all(people)
    db.commit()
    rows = [
        RawRequestRow(
            point_no=p.point_no,
            personal_no=p.personal_no,
            name=p.name,
            account_type="person",
            amount="0",
            carry="0",
        )
        for p in people[:2000]
    ]
    review = review_rows(db, "2026-08", rows)
    absent = reconcile(
        "2026-08",
        review.analysis.changes,
        {f"{i:08d}": "0" for i in range(90000000, 90002000)},
        {},
        [],
    )
    values = [
        ("month", "2026-08"),
        ("request_key", uuid.uuid4().hex),
        ("review_token", review.token),
    ]
    for i, row in enumerate(review.raw_rows):
        for key in row.__dataclass_fields__:
            values.append((f"{key}_{i}", getattr(row, key)))
        values.append((f"link_person_{i}", ""))
    for point, binding in absent.bindings.items():
        values.extend([(f"deactivated_carry_{point}", "0"), (f"absent_binding_{point}", binding)])
    values.extend(("preserved_absent_carry", token) for token in absent.preserved)
    assert len(values) > 35000
    response = auth_client.post(
        "/drafts/save", files=[(key, (None, value)) for key, value in values]
    )
    assert response.status_code == 200, response.text
    draft = db.get(MonthlyDraft, response.json()["draft_id"])
    stored = json.loads(draft.payload_json)
    assert (
        len(stored["rows"]) == len(stored["deactivated"]) == len(stored["absent_bindings"]) == 2000
    )
    assert len(stored["preserved_absent_carries"]) == 2000
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
