"""프로필 차이는 표시된 값을 명시 선택하고, 장부 반영은 최종 확정에서만 한다."""

import json

import pytest
from sqlalchemy import func, select

from app.models import BalanceRecord, MonthlyDraft, MonthlySnapshot, Person
from tests.factories import make_person, make_team
from tests.monthly_helpers import resolve_profile_choices, review_fields


def request_row(client, **changes):
    values = {
        "month": "2026-08",
        "row_id_0": "profile-choice-row",
        "point_no_0": "",
        "name_0": "합성대원",
        "personal_no_0": "0011",
        "team_0": "요청팀",
        "grade_0": "소방교",
        "account_type_0": "person",
        "amount_0": "100",
        "carry_0": "35",
        "note_0": "\n첫째\n\n둘째\n",
        "source_line_0": "합성 요청 원문",
    }
    values.update(changes)
    return client.post("/monthly/review", data=values)


def existing(db, **changes):
    values = {
        "name": "합성대원",
        "personal_no": "0011",
        "point_no": "00000011",
        "team": make_team(db, "현재팀"),
        "grade": "소방위",
    }
    values.update(changes)
    return make_person(db, **values)


def choose(client, response, field, side="incoming", **changes):
    values = review_fields(response)
    values.update(changes)
    values["profile_choice"] = f"{values['row_id_0']}:{field}:{side}"
    return client.post("/monthly/choose", data=values)


def link(client, response, person):
    values = review_fields(response)
    values.update(link_row=values["row_id_0"], link_person_0=f"{person.id}:{person.version}")
    return client.post("/monthly/link", data=values)


def confirm(client, response, **changes):
    values = review_fields(response)
    values.update(ack_warnings="yes", **changes)
    return client.post("/monthly/confirm", data=values, follow_redirects=False)


def test_manual_link_keeps_input_until_per_field_choices_and_confirm(auth_client, db):
    person = existing(db)
    linked = link(auth_client, request_row(auth_client, personal_no_0="0099"), person)
    values = review_fields(linked)
    assert values["point_no_0"] == person.point_no
    assert values["personal_no_0"] == "0099" and values["team_0"] == "요청팀"
    assert values["grade_0"] == "소방교" and values["carry_0"] == ""
    assert values["note_0"] == "\n첫째\n\n둘째\n"
    assert values["source_line_0"] == "합성 요청 원문"
    assert not values["review_token"]
    assert confirm(auth_client, linked).status_code == 400
    selected = choose(auth_client, linked, "personal_no")
    selected = choose(auth_client, selected, "team", "current")
    selected = choose(auth_client, selected, "grade")
    db.expire_all()
    assert person.personal_no == "0011" and person.team.name == "현재팀"
    assert person.grade == "소방위"
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    assert confirm(auth_client, selected, carry_0="17").status_code == 303
    db.expire_all()
    assert person.personal_no == "0099" and person.team.name == "현재팀"
    assert person.grade == "소방교"
    record = db.scalar(select(BalanceRecord))
    assert record.total == 117 and record.note == values["note_0"]


def test_auto_link_requires_profile_choices_preserves_carry_and_replays(auth_client, db):
    person = existing(db)
    response = request_row(auth_client)
    values = review_fields(response)
    assert values["link_state_0"].startswith("auto:")
    assert values["team_0"] == "요청팀" and values["carry_0"] == "35"
    assert not values["review_token"]
    assert confirm(auth_client, response).status_code == 400
    selected = resolve_profile_choices(auth_client, response)
    assert review_fields(selected)["review_token"]
    assert review_fields(selected)["carry_0"] == "35"
    ready = review_fields(selected)
    ready["ack_warnings"] = "yes"
    for _ in range(2):
        assert (
            auth_client.post("/monthly/confirm", data=ready, follow_redirects=False).status_code
            == 303
        )
    db.expire_all()
    assert person.team.name == "요청팀" and person.grade == "소방교"
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 1
    assert db.scalar(select(BalanceRecord.total)) == 135


def test_selected_values_and_original_alternatives_survive_autosave_and_resume(auth_client, db):
    existing(db)
    selected = choose(auth_client, request_row(auth_client), "team", "current")
    values = review_fields(selected)
    saved = auth_client.post("/drafts/save", data=values)
    assert saved.status_code == 200
    reopened = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(reopened)["team_0"] == "현재팀"
    switched = choose(auth_client, reopened, "team", "incoming")
    assert review_fields(switched)["team_0"] == "요청팀"
    assert review_fields(switched)["carry_0"] == "35"


def test_stale_profile_choice_rejects_unseen_master_and_keeps_original_input(auth_client, db):
    person = existing(db)
    response = choose(auth_client, request_row(auth_client), "team", "current")
    person.team = make_team(db, "변경된현재팀")
    person.version += 1
    db.commit()
    stale = choose(auth_client, response, "grade", "current")
    assert stale.status_code == 409
    values = review_fields(stale)
    assert values["team_0"] == "요청팀" and values["grade_0"] == "소방교"
    assert values["carry_0"] == "" and not values["review_token"]
    assert "변경된현재팀" in stale.text
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_balance_only_version_change_does_not_reset_profile_choices(auth_client, db):
    person = existing(db)
    response = choose(auth_client, request_row(auth_client), "team", "current")
    person.current_amount = 90
    person.version += 1
    db.commit()
    selected = choose(auth_client, response, "grade", "current")
    assert selected.status_code == 200
    values = review_fields(selected)
    assert values["team_0"] == "현재팀" and values["grade_0"] == "소방위"
    assert values["carry_0"] == "35" and values["review_token"]


def test_retarget_restores_request_values_instead_of_previous_current_choice(auth_client, db):
    first = existing(db)
    second = make_person(
        db, name="다른대원", personal_no="0022", point_no="00000022", team=make_team(db, "다른팀")
    )
    selected = choose(auth_client, request_row(auth_client), "team", "current")
    changed = link(auth_client, selected, second)
    values = review_fields(changed)
    assert values["point_no_0"] == second.point_no
    assert values["name_0"] == first.name and values["personal_no_0"] == first.personal_no
    assert values["team_0"] == "요청팀" and values["grade_0"] == "소방교"
    assert values["carry_0"] == "" and not values["review_token"]
    assert "다른팀" in changed.text


def test_direct_edit_resets_only_that_choice_and_identity_edit_clears_carry(auth_client, db):
    person = existing(db)
    response = link(auth_client, request_row(auth_client, personal_no_0="0099"), person)
    selected = resolve_profile_choices(auth_client, response, side="current")
    values = review_fields(selected)
    values.update(team_0="수정입력팀", carry_0="55")
    changed = auth_client.post("/monthly/review", data=values)
    changed_values = review_fields(changed)
    assert changed_values["personal_no_0"] == person.personal_no
    assert changed_values["grade_0"] == person.grade
    assert changed_values["team_0"] == "수정입력팀" and changed_values["carry_0"] == "55"
    assert not changed_values["review_token"]
    selected = choose(auth_client, changed, "team")
    values = review_fields(selected)
    values["personal_no_0"] = "0033"
    changed = auth_client.post("/monthly/review", data=values)
    assert review_fields(changed)["carry_0"] == ""
    assert review_fields(changed)["personal_no_0"] == "0033"
    assert not review_fields(changed)["review_token"]


@pytest.mark.parametrize(
    "side,team,grade", [("current", "현재팀", "소방위"), ("incoming", None, "")]
)
def test_blank_optional_profile_requires_explicit_keep_or_clear(auth_client, db, side, team, grade):
    person = existing(db)
    response = request_row(auth_client, team_0="", grade_0="")
    assert not review_fields(response)["review_token"]
    selected = resolve_profile_choices(auth_client, response, side=side)
    assert confirm(auth_client, selected).status_code == 303
    db.expire_all()
    assert (person.team.name if person.team else None) == team
    assert person.grade == grade


def test_missing_required_profile_can_choose_current_and_type_change_stays_guarded(auth_client, db):
    person = existing(db)
    response = request_row(
        auth_client,
        point_no_0=person.point_no,
        name_0="",
        personal_no_0="",
        account_type_0="shared",
    )
    rejected = choose(auth_client, response, "account_type", "incoming")
    assert rejected.status_code == 409 and "유형 변경" in rejected.text
    selected = resolve_profile_choices(auth_client, rejected, side="current")
    values = review_fields(selected)
    assert values["name_0"] == person.name and values["personal_no_0"] == person.personal_no
    assert values["account_type_0"] == "person" and values["carry_0"] == ""
    assert values["review_token"]
    assert confirm(auth_client, selected, carry_0="0").status_code == 303
    db.expire_all()
    assert person.account_type == "person"


@pytest.mark.parametrize("state", ["", "tampered-signature"])
def test_missing_or_tampered_choices_cannot_confirm_unapproved_difference(auth_client, db, state):
    existing(db)
    selected = resolve_profile_choices(auth_client, request_row(auth_client))
    values = review_fields(selected)
    assert values["review_token"]
    values["profile_review_0"] = state
    rejected = auth_client.post("/monthly/confirm", data=values)
    assert rejected.status_code == 409
    assert not review_fields(rejected)["review_token"]
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_legacy_draft_without_profile_state_preserves_inputs_and_requires_choices(auth_client, db):
    person = existing(db)
    response = request_row(auth_client)
    values = review_fields(response)
    draft = db.get(MonthlyDraft, values["draft_id"])
    payload = json.loads(draft.payload_json)
    payload["rows"][0].pop("profile_review", None)
    payload["rows"][0].update(team="옛초안입력팀", carry="77", note="\n옛 입력\n\n보존")
    draft.payload_json = json.dumps(payload, ensure_ascii=False)
    db.commit()
    reopened = auth_client.get("/drafts/" + draft.id)
    values = review_fields(reopened)
    assert values["point_no_0"] == person.point_no
    assert values["team_0"] == "옛초안입력팀" and values["carry_0"] == "77"
    assert values["note_0"] == "\n옛 입력\n\n보존"
    assert not values["review_token"]
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_explicit_new_restores_incoming_and_rejects_existing_number(auth_client, db):
    person = existing(db)
    selected = choose(auth_client, request_row(auth_client), "team", "current")
    values = review_fields(selected)
    values["new_row"] = values["row_id_0"]
    response = auth_client.post("/monthly/new", data=values)
    values = review_fields(response)
    assert values["point_no_0"] == "" and values["link_state_0"] == "new"
    assert values["team_0"] == "요청팀" and values["carry_0"] == ""
    assert confirm(auth_client, response).status_code == 400
    values["point_no_0"] = person.point_no
    rejected = auth_client.post("/monthly/review", data=values)
    assert rejected.status_code == 400 and "이미 등록된 포인트번호" in rejected.text
    values = review_fields(rejected)
    values.update(point_no_0="00000099", carry_0="12")
    ready = auth_client.post("/monthly/review", data=values)
    assert review_fields(ready)["link_state_0"] == "new"
    assert (
        confirm(auth_client, ready, **{f"deactivated_carry_{person.point_no}": "20"}).status_code
        == 303
    )
    assert db.scalar(select(func.count(Person.id))) == 2


def test_stale_row_binding_and_unreviewed_edit_cannot_apply_choice(auth_client, db):
    existing(db)
    response = request_row(auth_client)
    stale = choose(auth_client, response, "team", "current", team_0="사용자가방금수정")
    assert stale.status_code == 409
    assert review_fields(stale)["team_0"] == "사용자가방금수정"
    values = review_fields(response)
    values["row_id_0"] = "copied-other-row"
    values["profile_choice"] = "copied-other-row:team:current"
    rejected = auth_client.post("/monthly/choose", data=values)
    assert rejected.status_code == 409
    assert not review_fields(rejected)["review_token"]
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
