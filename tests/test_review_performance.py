"""검수 중복 제거가 승인·잠금·실패 원문 보존의 HTTP 경계를 바꾸지 않는다."""

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as db_module
from app.models import BalanceRecord, MonthlyDraft, Person, Team
from app.services import drafts
from tests.factories import make_person
from tests.monthly_helpers import resolve_profile_choices, review_fields
from tests.test_monthly_profile_choices import existing, request_row


def request_data():
    return {
        "month": "2026-08",
        "row_id_0": "synthetic-review",
        "point_no_0": "00000011",
        "personal_no_0": "0011",
        "name_0": "합성대원",
        "team_0": "",
        "grade_0": "",
        "amount_0": "100",
        "carry_0": "35",
        "note_0": "\n첫째\n\n둘째\n",
        "account_type_0": "person",
        "source_line_0": "합성 요청 원문",
    }


def test_explicit_review_issues_token_but_autosave_only_compares_it(auth_client, db):
    response = auth_client.post("/monthly/review", data=request_data())
    values = review_fields(response)
    draft = db.get(MonthlyDraft, values["draft_id"])
    payload = json.loads(draft.payload_json)
    original_token = payload["review_token"]
    assert original_token and draft.review_state == "reviewed"

    values["amount_0"] = "101"
    saved = auth_client.post("/drafts/save", data=values)
    assert saved.status_code == 200
    values.update(saved.json())
    db.expire_all()
    payload = json.loads(draft.payload_json)
    assert payload["review_token"] == original_token
    assert payload["rows"][0]["amount"] == "101"
    assert draft.review_state == "needs_review"
    assert auth_client.post("/monthly/confirm", data=values).status_code == 409

    # 명시 검수는 브라우저의 오래된 토큰 대신 잠금 안에서 새로 계산한 토큰을 저장한다.
    reviewed = auth_client.post("/monthly/review", data=values)
    assert reviewed.status_code == 200
    db.expire_all()
    payload = json.loads(draft.payload_json)
    assert payload["review_token"] and payload["review_token"] != original_token
    assert draft.review_state == "reviewed"
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_review_uses_profile_changed_before_write_lock(auth_client, db, monkeypatch):
    person = existing(db)
    selected = resolve_profile_choices(auth_client, request_row(auth_client), side="current")
    values = review_fields(selected)
    person_id = person.id
    start_write = drafts.start_write

    def change_before_lock(session):
        session.rollback()
        with Session(db_module.engine) as other:
            current = other.get(Person, person_id)
            current.team = Team(name="잠금 전 변경팀", color="#123456")
            current.version += 1
            other.commit()
        start_write(session)

    monkeypatch.setattr(drafts, "start_write", change_before_lock)
    response = auth_client.post("/monthly/review", data=values)
    assert response.status_code == 200
    current = review_fields(response)
    assert "잠금 전 변경팀" in response.text
    assert current["team_0"] == "요청팀" and current["grade_0"] == "소방교"
    assert current["carry_0"] == "" and not current["review_token"]
    draft = db.get(MonthlyDraft, current["draft_id"])
    payload = json.loads(draft.payload_json)
    assert payload["rows"][0]["team"] == "요청팀"
    assert payload["rows"][0]["carry"] == ""
    assert not payload["review_token"] and draft.review_state == "incomplete"
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_redirect_get_rechecks_database_after_draft_commit(auth_client, db):
    person = make_person(db, "0011", "합성대원", point_no="00000011", grade="")
    response = auth_client.post("/monthly/review", data=request_data(), follow_redirects=False)
    assert response.status_code == 303
    draft_id = response.headers["location"].rsplit("/", 1)[1]
    draft = db.get(MonthlyDraft, draft_id)
    saved_payload = draft.payload_json
    assert json.loads(saved_payload)["review_token"]

    person.name = "저장 뒤 변경된 이름"
    person.version += 1
    db.commit()
    reopened = auth_client.get(response.headers["location"])
    values = review_fields(reopened)
    assert reopened.status_code == 200
    assert "저장 뒤 변경된 이름" in reopened.text
    assert values["name_0"] == "합성대원"
    assert values["carry_0"] == "" and not values["review_token"]
    db.expire_all()
    assert draft.payload_json == saved_payload
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_failed_review_save_rolls_back_and_preserves_submitted_input(auth_client, db, monkeypatch):
    values = review_fields(auth_client.post("/monthly/review", data=request_data()))
    draft = db.get(MonthlyDraft, values["draft_id"])
    before, version = draft.payload_json, draft.version
    values.update(note_0="\n저장할 새 원문\n\n끝\n", carry_0="12.5")
    commit = Session.commit

    def fail_draft_commit(session):
        if any(isinstance(item, MonthlyDraft) for item in session.dirty):
            raise OSError("synthetic private failure")
        commit(session)

    monkeypatch.setattr(Session, "commit", fail_draft_commit)
    response = auth_client.post("/monthly/review", data=values)
    assert response.status_code == 500
    assert "synthetic private failure" not in response.text
    restored = review_fields(response)
    for field in ("row_id_0", "name_0", "note_0", "carry_0", "source_line_0"):
        assert restored[field] == values[field]
    assert restored["draft_id"] == values["draft_id"]
    assert restored["draft_version"] == values["draft_version"]
    db.expire_all()
    assert draft.payload_json == before and draft.version == version
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_stale_explicit_review_preserves_page_and_newer_saved_draft(auth_client, db):
    original = review_fields(auth_client.post("/monthly/review", data=request_data()))
    assert (
        auth_client.post("/drafts/save", data={**original, "note_0": "다른 탭 저장"}).status_code
        == 200
    )
    draft = db.get(MonthlyDraft, original["draft_id"])
    before, version = draft.payload_json, draft.version
    stale = {**original, "note_0": "\n오래된 탭 입력\n\n보존\n", "carry_0": "77"}
    response = auth_client.post("/monthly/review", data=stale)
    assert response.status_code == 409
    restored = review_fields(response)
    assert restored["note_0"] == stale["note_0"] and restored["carry_0"] == "77"
    assert restored["draft_version"] == original["draft_version"]
    db.expire_all()
    assert draft.payload_json == before and draft.version == version
