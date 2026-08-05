from tests.factories import make_person, make_team


def test_teams_page_empty(auth_client):
    resp = auth_client.get("/teams")
    assert resp.status_code == 200
    assert "등록된 팀이 없습니다" in resp.text


def test_create_team(auth_client, db):
    resp = auth_client.post(
        "/teams", data={"name": "구조대", "color": "#c0392b"}, follow_redirects=False
    )
    assert resp.status_code == 303
    resp = auth_client.get("/teams")
    assert "구조대" in resp.text


def test_create_team_duplicate(auth_client, db):
    make_team(db, "구조대")
    resp = auth_client.post("/teams", data={"name": "구조대", "color": "#c0392b"})
    assert resp.status_code == 400
    assert "이미 존재합니다" in resp.text


def test_create_team_empty_name(auth_client, db):
    resp = auth_client.post("/teams", data={"name": "  ", "color": "#c0392b"})
    assert resp.status_code == 400
    assert "팀 이름을 입력" in resp.text


def test_delete_team_empty(auth_client, db):
    team = make_team(db, "행정지원팀")
    resp = auth_client.post(f"/teams/{team.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert "행정지원팀" not in auth_client.get("/teams").text


def test_delete_team_releases_persons(auth_client, db):
    team = make_team(db, "구조대")
    person = make_person(db, team=team)
    resp = auth_client.post(f"/teams/{team.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(person)
    assert person.team_id is None


def test_delete_team_missing(auth_client, db):
    resp = auth_client.post("/teams/9999/delete", follow_redirects=False)
    assert resp.status_code == 303


def test_teams_requires_login(client):
    resp = client.get("/teams", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
