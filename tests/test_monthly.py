from sqlalchemy import select

from app.ai.mock import MockProvider
from app.models import MonthlySnapshot, Person
from app.services.parsing import parse_pasted
from tests.factories import make_person


def test_mock_provider_returns_rows():
    provider = MockProvider()
    rows = provider.extract_table(b"", "test.png")
    assert len(rows) == 3
    assert rows[0].name == "김소방"


def test_mock_provider_custom_json():
    provider = MockProvider(mock_json='[{"personal_no": "1", "name": "홍길동", "amount": 1000}]')
    rows = provider.extract_table(b"", "x.png")
    assert len(rows) == 1
    assert rows[0].amount == 1000


def test_parse_pasted_spec_order():
    text = "1\t1팀\t김소방\t소방경\t50000\t101\t\n2\t2팀\t이소방\t소방위\t30000\t102\t"
    rows = parse_pasted(text)
    assert len(rows) == 2
    assert rows[0].personal_no == "101"
    assert rows[0].team == "1팀"
    assert rows[1].amount == 30000


def test_parse_pasted_six_columns():
    text = "1팀\t김소방\t소방경\t50000\t101\t비고"
    rows = parse_pasted(text)
    assert len(rows) == 1
    assert rows[0].personal_no == "101"
    assert rows[0].note == "비고"


def test_parse_pasted_skips_empty_and_bad_lines():
    text = "1팀\t김소방\n\n\t\t\t\n"
    rows = parse_pasted(text)
    assert len(rows) == 0


def test_parse_pasted_amount_with_commas():
    rows = parse_pasted("1팀\t김소방\t소방경\t50,000원\t101\t")
    assert rows[0].amount == 50000


def test_monthly_page_empty(auth_client):
    resp = auth_client.get("/monthly")
    assert resp.status_code == 200
    assert "아직 처리된 월이 없습니다" in resp.text


def test_upload_with_pasted_text(auth_client):
    text = "1팀\t김소방\t소방경\t50000\t101\t"
    resp = auth_client.post("/monthly/upload", data={"month": "2026-07", "pasted": text})
    assert resp.status_code == 200
    assert "요청서 검수" in resp.text
    assert "김소방" in resp.text
    assert "신규" in resp.text


def test_upload_empty_returns_error(auth_client):
    resp = auth_client.post("/monthly/upload", data={"month": "2026-07"})
    assert resp.status_code == 400
    assert "인식된 인원이 없습니다" in resp.text


def test_upload_with_image_uses_provider(auth_client, monkeypatch):
    from app.routers import monthly as monthly_router

    monkeypatch.setattr(monthly_router, "get_provider", lambda: MockProvider())
    resp = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-07"},
        files={"file": ("req.png", b"fake-image", "image/png")},
    )
    assert resp.status_code == 200
    assert "김소방" in resp.text


def test_confirm_creates_snapshot_and_syncs(auth_client, db):
    data = {
        "month": "2026-07",
        "personal_no_0": "101",
        "name_0": "김소방",
        "team_0": "1팀",
        "grade_0": "소방경",
        "amount_0": "50000",
        "carry_101|김소방": "10000",
    }
    resp = auth_client.post("/monthly/confirm", data=data, follow_redirects=False)
    assert resp.status_code == 303
    person = db.scalar(select(Person).where(Person.personal_no == "101"))
    assert person is not None
    assert person.status == "active"
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07"))
    assert snapshot is not None
    record = snapshot.records[0]
    assert record.carry_balance == 10000
    assert record.amount == 50000
    assert record.usage == 0
    assert record.total == 60000


def test_confirm_usage_calculation_with_previous_month(auth_client, db):
    data = {
        "month": "2026-06",
        "personal_no_0": "101",
        "name_0": "김소방",
        "team_0": "1팀",
        "grade_0": "소방경",
        "amount_0": "50000",
        "carry_101|김소방": "10000",
    }
    auth_client.post("/monthly/confirm", data=data)
    data["month"] = "2026-07"
    data["carry_101|김소방"] = "4000"
    resp = auth_client.post("/monthly/confirm", data=data, follow_redirects=False)
    assert resp.status_code == 303
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07"))
    record = snapshot.records[0]
    assert record.usage == 60000 - 4000
    assert record.total == 50000 + 4000


def test_confirm_duplicate_month_rejected(auth_client, db):
    data = {
        "month": "2026-07",
        "personal_no_0": "101",
        "name_0": "김소방",
        "team_0": "1팀",
        "grade_0": "소방경",
        "amount_0": "50000",
        "carry_101|김소방": "0",
    }
    auth_client.post("/monthly/confirm", data=data)
    resp = auth_client.post("/monthly/confirm", data=data)
    assert resp.status_code == 400
    assert "이미 처리되었습니다" in resp.text


def test_confirm_invalid_month_rejected(auth_client):
    resp = auth_client.post("/monthly/confirm", data={"month": "2026-7"})
    assert resp.status_code == 400
    assert "월 형식" in resp.text


def test_confirm_no_rows_rejected(auth_client):
    resp = auth_client.post("/monthly/confirm", data={"month": "2026-07"})
    assert resp.status_code == 400
    assert "인원 행이 없습니다" in resp.text


def test_confirm_deactivates_missing_person(auth_client, db):
    make_person(db, "999", "기존인원")
    data = {
        "month": "2026-07",
        "personal_no_0": "101",
        "name_0": "김소방",
        "team_0": "1팀",
        "grade_0": "",
        "amount_0": "0",
        "carry_101|김소방": "0",
        "carry_999|기존인원": "3000",
    }
    auth_client.post("/monthly/confirm", data=data)
    old = db.scalar(select(Person).where(Person.personal_no == "999"))
    assert old.status == "inactive"
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07"))
    keys = {(r.person_id) for r in snapshot.records}
    assert old.id in keys


def test_confirm_returns_balance_after_return(auth_client, db):
    make_person(db, "101", "김소방", status="inactive")
    data = {
        "month": "2026-07",
        "personal_no_0": "101",
        "name_0": "김소방",
        "team_0": "1팀",
        "grade_0": "",
        "amount_0": "50000",
        "carry_101|김소방": "7000",
    }
    auth_client.post("/monthly/confirm", data=data)
    person = db.scalar(select(Person).where(Person.personal_no == "101"))
    assert person.status == "active"


def test_record_edit_recomputes(auth_client, db):
    person = make_person(db, "101", "김소방")
    data = {
        "month": "2026-06",
        "personal_no_0": "101",
        "name_0": "김소방",
        "team_0": "1팀",
        "grade_0": "",
        "amount_0": "50000",
        "carry_101|김소방": "10000",
    }
    auth_client.post("/monthly/confirm", data=data)
    resp = auth_client.post(
        f"/people/{person.id}/record-edit",
        data={"carry_balance": "3000", "amount": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    from app.services.balance import last_record_for_person

    record = last_record_for_person(db, person)
    assert record is not None
    assert record.carry_balance == 3000
    assert record.usage == 0
    assert record.total == 3000


def test_record_edit_no_record_noop(auth_client, db):
    person = make_person(db, "101", "김소방")
    resp = auth_client.post(
        f"/people/{person.id}/record-edit",
        data={"carry_balance": "100", "amount": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/people/{person.id}"


def test_monthly_requires_login(client):
    resp = client.get("/monthly", follow_redirects=False)
    assert resp.status_code == 303


def test_monthly_done_message(auth_client):
    resp = auth_client.get("/monthly?done=1")
    assert "처리가 완료되었습니다" in resp.text


def test_parse_row_fields_over_100_rows(auth_client):
    from starlette.datastructures import FormData

    from app.routers.monthly import _parse_row_fields

    form_data = []
    for i in range(101):
        form_data.append((f"personal_no_{i}", f"10{i:03d}"))
        form_data.append((f"name_{i}", f"인원{i}"))
        form_data.append((f"team_{i}", "1팀"))
        form_data.append((f"grade_{i}", ""))
        form_data.append((f"amount_{i}", "50000"))
    form = FormData(form_data)
    rows = _parse_row_fields(form)
    assert len(rows) == 101
    assert rows[100].personal_no == "10100"
