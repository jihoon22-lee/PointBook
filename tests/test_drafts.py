"""R20/R21: 원장과 분리된 서버 초안의 수명·경합·인증 HTTP 계약."""

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import db as db_module
from app.config import get_settings
from app.models import BalanceRecord, LedgerOperation, LedgerState, MonthlyDraft, Person, utcnow
from tests.monthly_helpers import review_fields


def start(client):
    response = client.post(
        "/monthly/review",
        data={
            "month": "2026-08",
            "point_no_0": "00001101",
            "account_type_0": "person",
            "personal_no_0": "1101",
            "name_0": "합성초안",
            "team_0": "",
            "grade_0": "",
            "amount_0": "100",
            "carry_0": "",
            "note_0": "원문",
        },
    )
    assert response.status_code == 200
    return review_fields(response)


def save(client, values):
    return client.post("/drafts/save", data=values)


def test_autosave_preserves_raw_and_stable_identity_without_ledger_write(auth_client, db):
    values = start(auth_client)
    baseline = db.scalar(select(LedgerState.version))
    values.update(carry_0="12.5", name_0="한글 조합", note_0="=SUM(A1) 원문")
    response = save(auth_client, values)
    assert response.status_code == 200
    state = response.json()
    assert state["draft_version"] == int(values["draft_version"]) + 1
    recovered = review_fields(auth_client.get("/drafts/" + state["draft_id"]))
    for key in ["row_id_0", "carry_0", "name_0", "note_0"]:
        assert recovered[key] == values[key]
    assert db.scalar(select(func.count(Person.id))) == 0
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    assert db.scalar(select(LedgerState.version)) == baseline
    assert response.headers["cache-control"] == "no-store"


def test_other_device_and_database_reopen_restore_same_values(auth_client, db):
    values = start(auth_client)
    values["carry_0"] = "500"
    response = save(auth_client, values)
    draft_id = response.json()["draft_id"]
    # 새 엔진/세션으로 같은 파일의 영속 데이터를 읽는다.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    independent = create_engine(db_module.current_database_url())
    try:
        with Session(independent) as other_db:
            draft = other_db.get(MonthlyDraft, draft_id)
            assert json.loads(draft.payload_json)["rows"][0]["carry"] == "500"
    finally:
        independent.dispose()
    other = TestClient(auth_client.app)
    try:
        other.cookies.update(auth_client.cookies)
        assert review_fields(other.get("/drafts/" + draft_id))["carry_0"] == "500"
    finally:
        other.close()


def test_two_tabs_do_not_overwrite_and_stale_confirm_is_blocked(auth_client, db):
    original = start(auth_client)
    changed = {**original, "carry_0": "40"}
    assert save(auth_client, changed).status_code == 200
    stale = {**original, "carry_0": "99"}
    assert save(auth_client, stale).status_code == 409
    stale["ack_warnings"] = "yes"
    assert auth_client.post("/monthly/confirm", data=stale).status_code == 409
    draft = db.get(MonthlyDraft, original["draft_id"])
    assert json.loads(draft.payload_json)["rows"][0]["carry"] == "40"
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_fork_preserves_other_device_draft(auth_client, db):
    original = start(auth_client)
    changed = save(auth_client, {**original, "carry_0": "40"}).json()
    fork = save(auth_client, {**original, "carry_0": "99", "fork": "yes"})
    assert fork.status_code == 200 and fork.json()["draft_id"] != original["draft_id"]
    assert fork.json()["request_key"] != original["request_key"]
    assert review_fields(auth_client.get("/drafts/" + changed["draft_id"]))["carry_0"] == "40"
    assert db.scalar(select(func.count(MonthlyDraft.id))) == 2


def test_expiry_delete_and_retention_limit(auth_client, db, monkeypatch):
    values = start(auth_client)
    draft = db.get(MonthlyDraft, values["draft_id"])
    draft.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert auth_client.get("/drafts/" + draft.id).status_code == 410
    assert save(auth_client, values).status_code == 410
    monkeypatch.setattr(get_settings(), "draft_max_active", 1)
    fresh = start(auth_client)
    second = save(auth_client, {**fresh, "fork": "yes"})
    assert second.status_code == 409
    deleted = auth_client.post(
        "/drafts/" + fresh["draft_id"] + "/delete",
        data={"draft_version": fresh["draft_version"], "confirm_delete": "yes"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert auth_client.get("/drafts/" + fresh["draft_id"]).status_code == 410
    db.expire_all()
    assert db.get(MonthlyDraft, fresh["draft_id"]).payload_json == "{}"
    assert db.get(MonthlyDraft, draft.id).payload_json == "{}"


def test_delete_requires_confirmation_and_latest_version(auth_client):
    values = start(auth_client)
    path = "/drafts/" + values["draft_id"] + "/delete"
    assert auth_client.post(path, data=values).status_code == 400
    save(auth_client, values)
    assert auth_client.post(path, data={**values, "confirm_delete": "yes"}).status_code == 409


def test_confirmation_transitions_draft_atomically(auth_client, db):
    values = start(auth_client)
    values.update(carry_0="10", ack_warnings="yes")
    response = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert response.status_code == 303
    draft = db.get(MonthlyDraft, values["draft_id"])
    assert draft.status == "confirmed"
    assert draft.result_url == response.headers["location"]
    assert (
        auth_client.get("/drafts/" + draft.id, follow_redirects=False).headers["location"]
        == draft.result_url
    )
    assert save(auth_client, values).status_code == 409
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1
    assert db.scalar(select(BalanceRecord.total)) == 110


def test_failed_confirmation_keeps_draft_active(auth_client, db, monkeypatch):
    values = start(auth_client)
    values.update(carry_0="10", ack_warnings="yes")

    def fail():
        raise OSError("synthetic secret")

    monkeypatch.setattr("app.routers.monthly.backup_database", fail)
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 500 and "synthetic secret" not in response.text
    assert review_fields(response)["carry_0"] == "10"
    assert db.get(MonthlyDraft, values["draft_id"]).status == "active"
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_password_change_relogin_recovers_draft_and_csrf(auth_client, db):
    values = start(auth_client)
    old_cookie = auth_client.cookies.get("session")
    settings = get_settings()
    response = auth_client.post(
        "/settings",
        data={
            "current_password": settings.admin_password,
            "new_password": "draft-new-pass",
            "confirm_password": "draft-new-pass",
        },
    )
    assert response.status_code == 200
    auth_client.cookies.set("session", old_cookie)
    denied = auth_client.raw_request("POST", "/drafts/save", data=values, follow_redirects=False)
    assert denied.status_code in [303, 403]
    auth_client.cookies.clear()
    auth_client.post(
        "/login", data={"username": settings.admin_username, "password": "draft-new-pass"}
    )
    session = auth_client.get("/drafts/session")
    assert session.status_code == 200 and session.json()["csrf_token"]
    recovered = review_fields(auth_client.get("/drafts/" + values["draft_id"]))
    assert recovered["row_id_0"] == values["row_id_0"]
    assert save(auth_client, {**recovered, "carry_0": "25"}).status_code == 200


def test_auth_csrf_and_owner_boundary(client, auth_client, db):
    values = start(auth_client)
    assert (
        auth_client.raw_request("POST", "/drafts/save", data={"month": "2026-08"}).status_code
        == 403
    )
    draft = db.get(MonthlyDraft, values["draft_id"])
    # 다른 소유자의 키를 단일 관리자에 노출하지 않는다.
    from app.models import AdminUser

    other = AdminUser(username="synthetic-other", password_hash="unusable")
    db.add(other)
    db.flush()
    draft.owner_id = other.id
    db.commit()
    assert auth_client.get("/drafts/" + draft.id).status_code == 404
    assert save(auth_client, values).status_code == 404


def test_autosave_failure_is_explicit_and_does_not_change_saved_payload(
    auth_client, db, monkeypatch
):
    values = start(auth_client)
    before = db.get(MonthlyDraft, values["draft_id"]).payload_json

    def fail(*args, **kwargs):
        raise OSError("synthetic sensitive path")

    monkeypatch.setattr("app.routers.drafts.save_draft", fail)
    response = save(auth_client, {**values, "carry_0": "500"})
    assert response.status_code == 500
    assert "synthetic sensitive path" not in response.text
    db.expire_all()
    assert db.get(MonthlyDraft, values["draft_id"]).payload_json == before


def test_draft_size_limit_and_invalid_version(auth_client):
    values = start(auth_client)
    assert save(auth_client, {**values, "draft_version": "x"}).status_code == 409
    response = save(auth_client, {**values, "note_0": "a" * 20_001})
    assert response.status_code == 413
