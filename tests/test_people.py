from sqlalchemy import select

from app.models import Person
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


def test_search_with_empty_team_id(auth_client, db):
    make_person(db, "1001", "홍길동")
    resp = auth_client.get("/people?status=&team_id=&q=홍길동")
    assert resp.status_code == 200
    assert "홍길동" in resp.text


def test_filter_team_id_works(auth_client, db):
    team_a = make_team(db, "A팀")
    make_person(db, "1001", "갑", team=team_a)
    resp = auth_client.get(f"/people?team_id={team_a.id}")
    assert resp.status_code == 200
    assert "갑" in resp.text


def test_create_person_team_less(auth_client, db):
    resp = auth_client.post(
        "/people/new",
        data={
            "personal_no": "2001",
            "name": "팀없는사람",
            "grade": "",
            "team_id": "",
            "status": "active",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "팀없는사람" in auth_client.get("/people").text


def test_edit_person_team_less(auth_client, db):
    person = make_person(db, "1001", "홍길동")
    resp = auth_client.post(
        f"/people/{person.id}/edit",
        data={
            "personal_no": "1001",
            "name": "홍길동",
            "grade": "",
            "team_id": "",
            "status": "active",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(person)
    assert person.team_id is None


def test_person_detail_shows_balance_display_only(auth_client, db):
    person = make_person(db, "1001", "홍길동", status="active")
    person.current_carry_balance = 15000
    person.current_amount = 50000
    db.commit()
    resp = auth_client.get(f"/people/{person.id}")
    assert resp.status_code == 200
    assert "15,000원" in resp.text
    assert "50,000원" in resp.text
    assert "65,000원" in resp.text
    assert 'name="carry_balance"' not in resp.text
    assert 'name="amount"' not in resp.text


def test_edit_form_has_balance_fields(auth_client, db):
    person = make_person(db, "1001", "홍길동")
    resp = auth_client.get(f"/people/{person.id}/edit")
    assert resp.status_code == 200
    assert 'name="carry_balance"' in resp.text
    assert 'name="amount"' in resp.text


def test_create_person_with_balance(auth_client, db):
    resp = auth_client.post(
        "/people/new",
        data={
            "personal_no": "3001",
            "name": "잔액있는사람",
            "grade": "",
            "team_id": "",
            "status": "active",
            "carry_balance": "15000",
            "amount": "50000",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    person = db.scalar(select(Person).where(Person.personal_no == "3001"))
    assert person is not None
    assert person.current_carry_balance == 15000
    assert person.current_amount == 50000


def test_edit_person_balance(auth_client, db):
    person = make_person(db, "1001", "홍길동")
    resp = auth_client.post(
        f"/people/{person.id}/edit",
        data={
            "personal_no": "1001",
            "name": "홍길동",
            "grade": "",
            "team_id": "",
            "status": "active",
            "carry_balance": "7000",
            "amount": "30000",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(person)
    assert person.current_carry_balance == 7000
    assert person.current_amount == 30000


def test_edit_person_updates_current_without_snapshot(auth_client, db):
    person = make_person(db, "1001", "홍길동")
    resp = auth_client.post(
        f"/people/{person.id}/edit",
        data={
            "personal_no": "1001",
            "name": "홍길동",
            "grade": "",
            "team_id": "",
            "status": "active",
            "carry_balance": "9000",
            "amount": "20000",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(person)
    assert person.current_carry_balance == 9000
    assert person.current_amount == 20000


def test_edit_person_preserves_earlier_records(auth_client, db):
    from app.models import BalanceRecord
    from app.services.balance import create_monthly_snapshot

    person = make_person(db, "1001", "홍길동")
    create_monthly_snapshot(
        db,
        "2026-05",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=1000, usage=0, total=1000)],
    )
    create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=2000, usage=0, total=2000)],
    )
    resp = auth_client.post(
        f"/people/{person.id}/edit",
        data={
            "personal_no": "1001",
            "name": "홍길동",
            "grade": "",
            "team_id": "",
            "status": "active",
            "carry_balance": "1500",
            "amount": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    records = sorted(person.balances, key=lambda b: b.snapshot.month)
    assert [r.total for r in records] == [1000, 1500]
    assert records[0].carry_balance == 0
    assert records[1].carry_balance == 1500
