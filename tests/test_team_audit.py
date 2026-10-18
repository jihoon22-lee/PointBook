"""팀 삭제로 바뀐 소속은 변경 이력과 인원 버전에 남는다."""

import json

from sqlalchemy import func, select

from app.models import LedgerOperation, LedgerState, Team
from tests.factories import make_person, make_team


def _ledger_version(db):
    return db.scalar(select(LedgerState.version))


def test_team_delete_records_audit_and_bumps_versions(auth_client, db):
    team = make_team(db, "합성삭제팀")
    team_id = team.id
    person = make_person(db, "301", "합성인원", team=team)
    shared = make_person(db, "", "합성공용", team=team, point_no="30000302", account_type="shared")
    versions = (person.version, shared.version)
    ledger_before = _ledger_version(db)
    response = auth_client.post(f"/teams/{team_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    assert db.get(Team, team_id) is None
    assert (person.team_id, shared.team_id) == (None, None)
    assert (person.version, shared.version) == (versions[0] + 1, versions[1] + 1)
    assert _ledger_version(db) == ledger_before + 1
    operation = db.scalar(select(LedgerOperation).where(LedgerOperation.kind == "team"))
    assert operation.reason == "팀 '합성삭제팀' 삭제"
    details = json.loads(operation.detail_json)
    assert details["team"]["name"] == "합성삭제팀"
    changes = {change["person_id"]: change for change in details["changes"]}
    assert set(changes) == {person.id, shared.id}
    assert changes[person.id]["before"]["team_name"] == "합성삭제팀"
    assert changes[person.id]["after"]["team_name"] == ""
    history = auth_client.get("/ledger").text
    assert "팀 삭제" in history
    detail = auth_client.get(f"/ledger/operations/{operation.request_key}")
    assert detail.status_code == 200
    assert "합성인원" in detail.text and "합성삭제팀" in detail.text


def test_missing_team_delete_writes_nothing(auth_client, db):
    response = auth_client.post("/teams/9999/delete", follow_redirects=False)
    assert response.status_code == 303
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_team_delete_without_backup_changes_nothing(auth_client, db, monkeypatch):
    from app.routers import teams as teams_router

    team = make_team(db, "합성보존팀")
    person = make_person(db, "303", "합성보존", team=team)
    monkeypatch.setattr(teams_router, "backup_database", lambda: None)
    response = auth_client.post(f"/teams/{team.id}/delete")
    assert response.status_code == 409
    assert "백업" in response.text
    db.expire_all()
    assert db.get(Team, team.id) is not None
    assert person.team_id == team.id
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_review_lists_teams_that_confirm_would_create(auth_client, db):
    existing = make_team(db, "합성기존팀")
    make_person(db, "401", "합성기존", team=existing)
    response = auth_client.post(
        "/monthly/review",
        data={
            "month": "2026-07",
            "point_no_0": "00000401",
            "account_type_0": "person",
            "personal_no_0": "401",
            "name_0": "합성기존",
            "team_0": "합성기존팀",
            "grade_0": "소방위",
            "amount_0": "100",
            "carry_0": "0",
            "point_no_1": "00000402",
            "account_type_1": "person",
            "personal_no_1": "402",
            "name_1": "합성신규",
            "team_1": "합성새팀",
            "grade_1": "",
            "amount_1": "100",
            "carry_1": "0",
            "link_state_1": "new",
        },
    )
    assert "확정 시 새로 만들 팀: 합성새팀" in response.text
    assert "확정 시 새로 만들 팀: 합성기존팀" not in response.text
    assert 'name="ack_warnings"' not in response.text
