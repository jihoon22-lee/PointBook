import pytest
from sqlalchemy import event, func, select

from app.models import BalanceRecord, MonthlySnapshot, Person
from app.services.balance import build_balance_records, create_monthly_snapshot
from app.services.parsing import RawRequestRow, parse_pasted_raw
from app.services.review import review_rows
from app.services.sync import RequestRow, analyze
from tests.factories import make_person
from tests.monthly_helpers import review_fields, reviewed_confirm


def data_row(**extra):
    return {
        "month": "2026-07",
        "point_no_0": "00000001",
        "personal_no_0": "11",
        "name_0": "합성 이름",
        "team_0": "팀 A",
        "grade_0": "소방사",
        "amount_0": "50,000",
        "carry_0": "10,000",
        "note_0": "검수 메모 <보존>",
        "account_type_0": "person",
        **extra,
    }


@pytest.mark.parametrize("bad", ["", "-1,000", "12.5", "5O000", "abc", "1,00", "1999999999999"])
@pytest.mark.parametrize("field", ["amount_0", "carry_0"])
def test_invalid_money_preserves_every_raw_cell_and_database(auth_client, db, field, bad):
    data = data_row(**{field: bad})
    response = reviewed_confirm(auth_client, data)
    assert response.status_code == 400
    returned = review_fields(response)
    for key, value in data.items():
        assert returned[key] == value
    assert db.scalar(select(func.count(Person.id))) == 0
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0


@pytest.mark.parametrize("month", ["2026-00", "2026-13", "0000-01", "2026-1", "２０２６-０１"])
def test_calendar_month_errors_preserve_rows(auth_client, month):
    data = data_row(month=month)
    response = auth_client.post("/monthly/review", data=data)
    assert response.status_code == 400
    assert review_fields(response)["month"] == month
    assert review_fields(response)["note_0"] == data["note_0"]


def test_confirm_requires_server_review(auth_client, db):
    response = auth_client.post("/monthly/confirm", data=data_row())
    assert response.status_code == 409
    assert "변경 예상" in response.text
    assert db.scalar(select(func.count(Person.id))) == 0


@pytest.mark.parametrize(
    "change",
    [{"point_no_0": "00000002"}, {"month": "2026-08"}, {"amount_0": "50001"}, {"note_0": "수정됨"}],
)
def test_changed_request_requires_new_review(auth_client, db, change):
    prepared = review_fields(auth_client.post("/monthly/review", data=data_row()))
    prepared.update(change)
    response = auth_client.post("/monthly/confirm", data=prepared)
    assert response.status_code == 409
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0
    new_review = review_fields(response)
    if "point_no_0" in change:
        # 다른 계정으로 옮긴 이전 이월은 재사용하지 않고 사람이 다시 입력한다.
        assert new_review["carry_0"] == ""
        new_review["carry_0"] = "10000"
    new_review["ack_warnings"] = "yes"
    assert (
        auth_client.post("/monthly/confirm", data=new_review, follow_redirects=False).status_code
        == 303
    )


def test_changed_database_requires_new_review(auth_client, db):
    person = make_person(db, "11", "합성 이름", point_no="00000001")
    prepared = review_fields(auth_client.post("/monthly/review", data=data_row()))
    person.name = "다른 탭 수정"
    db.commit()
    response = auth_client.post("/monthly/confirm", data=prepared)
    assert response.status_code == 409
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0


def test_deleted_row_gets_new_deactivation_carry_without_reusing_deleted_value(auth_client, db):
    old = make_person(db, "11", "기존", point_no="00000001")
    other = make_person(db, "22", "유지", point_no="00000002")
    data = data_row(
        point_no_1=other.point_no,
        personal_no_1=other.personal_no,
        name_1=other.name,
        amount_1="0",
        carry_1="0",
        account_type_1="person",
    )
    original = review_fields(auth_client.post("/monthly/review", data=data))
    second_id = original["row_id_1"]
    without_first = {key: value for key, value in original.items() if not key.endswith("_0")}
    response = auth_client.post("/monthly/review", data=without_first)
    assert response.status_code == 200
    prepared = review_fields(response)
    assert prepared["row_id_0"] == second_id
    assert prepared["deactivated_carry_00000001"] == ""
    prepared["ack_warnings"] = "yes"
    assert auth_client.post("/monthly/confirm", data=prepared).status_code == 400
    prepared["deactivated_carry_00000001"] = "321"
    assert (
        auth_client.post("/monthly/confirm", data=prepared, follow_redirects=False).status_code
        == 303
    )
    db.refresh(old)
    assert old.status == "inactive"
    assert old.current_carry_balance == 321


def test_bad_identifier_remains_editable_and_ordered(auth_client):
    data = data_row(
        point_no_0="틀린 번호",
        name_1="두번째",
        point_no_1="00000002",
        personal_no_1="22",
        amount_1="0",
        carry_1="0",
    )
    response = auth_client.post("/monthly/review", data=data)
    assert response.status_code == 400
    fields = review_fields(response)
    assert fields["point_no_0"] == "틀린 번호"
    assert fields["name_1"] == "두번째"
    assert fields["row_id_0"] != fields["row_id_1"]


def test_partial_paste_is_not_silently_dropped(auth_client):
    raw = parse_pasted_raw("팀\t이름\n\n\t\t\n")
    assert len(raw) == 1
    assert raw[0].name == "이름"
    response = auth_client.post("/monthly/upload", data={"month": "2026-07", "pasted": "팀\t이름"})
    assert response.status_code == 400
    assert review_fields(response)["source_line_0"] == "팀\t이름"


def test_shared_missing_personal_number_and_missing_shared_account(auth_client, db):
    shared = make_person(db, "", "누락 공용", point_no="00000009", account_type="shared")
    shared.current_carry_balance = 555
    db.commit()
    response = reviewed_confirm(
        auth_client, data_row(account_type_0="shared", personal_no_0=""), follow_redirects=False
    )
    assert response.status_code == 303
    created = db.scalar(select(Person).where(Person.point_no == "00000001"))
    assert created.account_type == "shared" and created.personal_no is None
    db.refresh(shared)
    assert shared.status == "active" and shared.current_carry_balance == 555


def test_unknown_type_and_duplicate_stable_ids_rejected(auth_client):
    for data in [
        data_row(account_type_0="oops"),
        data_row(
            row_id_0="same",
            row_id_1="same",
            point_no_1="00000002",
            personal_no_1="22",
            name_1="두번째",
            amount_1="0",
        ),
    ]:
        assert auth_client.post("/monthly/review", data=data).status_code == 400


def test_past_insertion_blocked_and_gap_warned(auth_client, db):
    person = make_person(db, "11", "합성 이름", point_no="00000001")
    create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, carry_balance=0, amount=100000, total=100000, usage=0)],
    )
    response = reviewed_confirm(auth_client, data_row(month="2026-05"))
    assert response.status_code == 400 and "이전에는 일반 확정" in response.text
    response = auth_client.post("/monthly/review", data=data_row(month="2026-08", carry_0="120000"))
    assert "기록 없는 달" in response.text
    prepared = review_fields(response)
    assert auth_client.post("/monthly/confirm", data=prepared).status_code == 400
    prepared["ack_warnings"] = "yes"
    assert (
        auth_client.post("/monthly/confirm", data=prepared, follow_redirects=False).status_code
        == 303
    )
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-08"))
    assert snapshot.records[0].usage == -20000
    assert db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == "2026-07")) is None


def test_future_and_external_reconciliation_require_explicit_ack(auth_client):
    response = auth_client.post(
        "/monthly/review", data=data_row(month="2099-01", expected_count="3", expected_amount="1")
    )
    assert (
        "미래 월" in response.text and "기대 인원" in response.text and "기대 총액" in response.text
    )
    prepared = review_fields(response)
    assert auth_client.post("/monthly/confirm", data=prepared).status_code == 400
    prepared["ack_warnings"] = "yes"
    assert (
        auth_client.post("/monthly/confirm", data=prepared, follow_redirects=False).status_code
        == 303
    )


def test_large_multipart_http_form(auth_client):
    data = {"month": "2026-07"}
    for i in range(201):
        data.update(
            {
                f"point_no_{i}": f"{i + 1:08d}",
                f"personal_no_{i}": str(i // 2),
                f"name_{i}": f"합성{i // 2}",
                f"amount_{i}": "0",
                f"carry_{i}": "0",
                f"account_type_{i}": "person",
            }
        )
    response = auth_client.post(
        "/monthly/review", files=[(key, (None, value)) for key, value in data.items()]
    )
    assert response.status_code == 200
    prepared = review_fields(response)
    assert prepared["row_id_200"]
    response = auth_client.post(
        "/monthly/confirm",
        files=[(key, (None, value)) for key, value in prepared.items()],
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.parametrize("count", [200, 1000])
def test_batch_analysis_and_previous_observations_query_budget(client, db, count):
    people = [
        Person(point_no=f"{i + 1:08d}", personal_no=str(i // 2), name=f"합성{i // 2}")
        for i in range(count)
    ]
    db.add_all(people)
    db.flush()
    ids = [p.id for p in people]
    create_monthly_snapshot(
        db,
        "2026-05",
        [
            BalanceRecord(person_id=p.id, amount=10, carry_balance=0, usage=0, total=10)
            for p in people
        ],
    )
    rows = [
        RequestRow(point_no=f"{i + 1:08d}", personal_no=str(i // 2), name=f"합성{i // 2}")
        for i in range(count)
    ]
    count_queries = []
    engine = db.get_bind()

    def observe(*args):
        count_queries.append(1)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        analyze(db, rows)
        records = build_balance_records(
            db, "2026-07", dict.fromkeys(ids, 20), dict.fromkeys(ids, 0)
        )
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(records) == count and all(r.usage == -10 for r in records)
    assert len(count_queries) <= 3


def test_review_detects_zero_previous_observation_without_current_fallback(client, db):
    person = make_person(db, "11", "합성", point_no="00000001")
    person.current_carry_balance = 999
    db.commit()
    create_monthly_snapshot(
        db,
        "2026-06",
        [BalanceRecord(person_id=person.id, amount=0, carry_balance=0, usage=0, total=0)],
    )
    result = review_rows(
        db,
        "2026-07",
        [RawRequestRow(point_no=person.point_no, personal_no="11", name="합성", amount="0")],
    )
    assert result.prev_totals[person.point_no] == 0


def test_reconciliation_allows_total_above_single_input_limit(auth_client):
    from app.services.validation import MAX_MONEY

    data = data_row(
        amount_0=str(MAX_MONEY),
        carry_0="0",
        expected_amount=str(MAX_MONEY * 2),
        point_no_1="00000002",
        personal_no_1="22",
        name_1="합성2",
        amount_1=str(MAX_MONEY),
        carry_1="0",
        account_type_1="person",
        expected_count="2",
    )
    response = auth_client.post("/monthly/review", data=data)
    assert response.status_code == 200
    assert "일치하지 않습니다" not in response.text
    assert (
        auth_client.post(
            "/monthly/confirm", data=review_fields(response), follow_redirects=False
        ).status_code
        == 303
    )


def test_missing_month_or_type_is_not_silently_defaulted(auth_client):
    data = data_row()
    del data["account_type_0"]
    assert auth_client.post("/monthly/review", data=data).status_code == 400
    response = auth_client.post(
        "/monthly/upload", data={"month": "", "pasted": "팀\t합성\t계급\t0\t11\t00000001"}
    )
    assert response.status_code == 400
    assert review_fields(response)["month"] == ""


def test_numeric_team_without_sequence_keeps_columns():
    from app.services.parsing import parse_pasted

    rows = parse_pasted("1\t합성\t계급\t0\t11\t00000001\t비고")
    assert rows[0].team == "1" and rows[0].name == "합성" and rows[0].note == "비고"
    # 둘 다 식별자처럼 보이는 경우 원문을 버리지 않고 헤더로 의미를 명시하게 한다.
    text = "1\t팀\t합성\t계급\t0\t12345678\t00000001"
    raw = parse_pasted_raw(text)
    assert raw[0].source_line == text and raw[0].source_issue
    rows = parse_pasted("순번\t팀\t이름\t계급\t금액\t개인번호\t포인트번호\n" + text)
    assert rows[0].personal_no == "12345678" and rows[0].point_no == "00000001"
