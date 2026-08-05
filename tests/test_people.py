from tests.factories import make_person, make_team


def test_people_page_empty(auth_client):
    resp = auth_client.get("/people")
    assert resp.status_code == 200
    assert "조건에 맞는 인원이 없습니다" in resp.text


def test_create_person(auth_client, db):
    team = make_team(db, "구조대")
    resp = auth_client.post(
        "/people/new",
        data={
            "personal_no": "1001",
            "name": "홍길동",
            "grade": "소방위",
            "team_id": str(team.id),
            "status": "active",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "홍길동" in auth_client.get("/people").text
    assert "구조대" in auth_client.get("/people").text


def test_create_person_missing_fields(auth_client):
    resp = auth_client.post("/people/new", data={"personal_no": "", "name": ""})
    assert resp.status_code == 400
    assert "개인번호와 이름은 필수" in resp.text


def test_create_person_duplicate_key(auth_client, db):
    make_person(db, "1001", "홍길동")
    resp = auth_client.post("/people/new", data={"personal_no": "1001", "name": "홍길동"})
    assert resp.status_code == 400
    assert "이미 등록된 인원" in resp.text


def test_people_filter_status(auth_client, db):
    make_person(db, "1001", "재직자")
    make_person(db, "1002", "퇴직자", status="inactive")
    resp = auth_client.get("/people?status=inactive")
    assert "퇴직자" in resp.text
    assert "재직자" not in resp.text
    resp = auth_client.get("/people?status=active")
    assert "재직자" in resp.text
    assert "퇴직자" not in resp.text


def test_people_filter_team(auth_client, db):
    team_a = make_team(db, "A팀")
    team_b = make_team(db, "B팀")
    make_person(db, "1001", "갑", team=team_a)
    make_person(db, "1002", "을", team=team_b)
    resp = auth_client.get(f"/people?team_id={team_b.id}")
    assert "을" in resp.text
    assert "갑" not in resp.text


def test_people_search(auth_client, db):
    make_person(db, "1001", "홍길동")
    make_person(db, "1002", "김철수")
    resp = auth_client.get("/people?q=김철수")
    assert "김철수" in resp.text
    assert "홍길동" not in resp.text


def test_person_detail(auth_client, db):
    person = make_person(db, "1001", "홍길동")
    resp = auth_client.get(f"/people/{person.id}")
    assert resp.status_code == 200
    assert "홍길동" in resp.text
    assert "아직 기록된 월별 데이터가 없습니다" in resp.text


def test_person_detail_missing(auth_client):
    resp = auth_client.get("/people/9999", follow_redirects=False)
    assert resp.status_code == 303


def test_edit_person_team_and_status(auth_client, db):
    team_a = make_team(db, "A팀")
    team_b = make_team(db, "B팀")
    person = make_person(db, "1001", "홍길동", team=team_a)
    resp = auth_client.post(
        f"/people/{person.id}/edit",
        data={
            "personal_no": "1001",
            "name": "홍길동",
            "grade": "소방경",
            "team_id": str(team_b.id),
            "status": "inactive",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(person)
    assert person.team_id == team_b.id
    assert person.status == "inactive"
    assert person.grade == "소방경"


def test_edit_person_duplicate_key_rejected(auth_client, db):
    make_person(db, "1001", "홍길동")
    other = make_person(db, "1002", "김철수")
    resp = auth_client.post(
        f"/people/{other.id}/edit",
        data={"personal_no": "1001", "name": "홍길동"},
    )
    assert resp.status_code == 400
    assert "이미 등록된 인원" in resp.text


def test_edit_person_missing(auth_client):
    resp = auth_client.post(
        "/people/9999/edit", data={"personal_no": "1", "name": "x"}, follow_redirects=False
    )
    assert resp.status_code == 303


def test_edit_person_form_missing(auth_client):
    resp = auth_client.get("/people/9999/edit", follow_redirects=False)
    assert resp.status_code == 303


def test_no_delete_person_route(auth_client):
    resp = auth_client.post("/people/1/delete")
    assert resp.status_code != 303 or "/people" not in resp.headers.get("location", "")


def test_people_requires_login(client):
    resp = client.get("/people", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_person_list_shows_team_badge(auth_client, db):
    team = make_team(db, "구조대", "#c0392b")
    make_person(db, team=team)
    resp = auth_client.get("/people")
    assert "#c0392b" in resp.text
