import json
from datetime import UTC, datetime

from sqlalchemy import event

from app.models import LedgerOperation
from app.services.tenure import tenure_labels
from tests.factories import make_person, make_team
from tests.test_legacy_status import legacy_record


def profile_event(db, person, at, before_status, after_status):
    before = {"status": before_status, "account_type": "person"} if before_status else None
    db.add(
        LedgerOperation(
            request_key=f"tenure-{person.id}-{at.isoformat()}",
            payload_hash="synthetic",
            kind="profile",
            reason="합성 재직 변경",
            result_url=f"/people/{person.id}",
            created_at=at,
            detail_json=json.dumps(
                {
                    "person_id": person.id,
                    "changes": [
                        {
                            "before": before,
                            "after": {"status": after_status, "account_type": "person"},
                        }
                    ],
                }
            ),
        )
    )
    db.commit()


def test_tenure_return_latest_first_inclusive_and_unobserved_gap(auth_client, db):
    person = make_person(db)
    legacy_record(db, person, "2025-05", 100)
    legacy_record(db, person, "2026-06", 100)
    legacy_record(db, person, "2026-07", 0)
    legacy_record(db, person, "2026-08", 100)
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "2개월 (26/08 ~ ), 1년 2개월 (25/05 ~ 26/06)"
    )


def test_tenure_closed_period_year_boundary_and_no_first_record_guess(auth_client, db):
    person = make_person(db, status="inactive")
    legacy_record(db, person, "2024-11", 0)
    legacy_record(db, person, "2025-01", 100)
    legacy_record(db, person, "2026-01", 0)
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "1년 (25/01 ~ 25/12)"
    )


def test_tenure_unknown_conflicting_current_status_and_shared(auth_client, db):
    no_history = make_person(db, "1001")
    conflict = make_person(db, "1002", status="inactive")
    shared = make_person(db, "1003", account_type="shared")
    never_active = make_person(db, "1004", status="inactive")
    legacy_record(db, conflict, "2026-08", 100)
    legacy_record(db, never_active, "2026-08", 0)
    labels = tenure_labels(
        db, [no_history, conflict, shared, never_active], through_month="2026-09"
    )
    assert labels == {
        no_history.id: "미확인",
        conflict.id: "최근 재직 기간 미확인",
        shared.id: "-",
        never_active.id: "미확인",
    }
    assert tenure_labels(db, [shared]) == {shared.id: "-"}


def test_profile_status_changes_use_kst_month_and_ignore_team_only_edit(auth_client, db):
    person = make_person(db)
    legacy_record(db, person, "2025-05", 100)
    profile_event(db, person, datetime(2026, 6, 30, 15, tzinfo=UTC), "active", "inactive")
    profile_event(db, person, datetime(2026, 7, 31, 15, tzinfo=UTC), "inactive", "active")
    profile_event(db, person, datetime(2026, 9, 1, tzinfo=UTC), "active", "active")
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "2개월 (26/08 ~ ), 1년 2개월 (25/05 ~ 26/06)"
    )


def test_initial_registration_and_same_month_last_status(auth_client, db):
    person = make_person(db)
    profile_event(db, person, datetime(2026, 8, 1, tzinfo=UTC), None, "active")
    profile_event(db, person, datetime(2026, 8, 2, tzinfo=UTC), "active", "inactive")
    profile_event(db, person, datetime(2026, 8, 3, tzinfo=UTC), "inactive", "active")
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == "2개월 (26/08 ~ )"


def test_future_evidence_not_used_and_bad_profile_event_ignored(auth_client, db):
    person = make_person(db)
    legacy_record(db, person, "2026-10", 100)
    db.add(
        LedgerOperation(
            request_key="malformed-tenure",
            payload_hash="synthetic",
            kind="profile",
            reason="합성 잘못된 이전 구조",
            result_url="/people",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
            detail_json=json.dumps({"person_id": person.id, "changes": [None]}),
        )
    )
    db.commit()
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == "미확인"


def test_tenure_visible_in_three_views_and_query_count_constant(auth_client, db, monkeypatch):
    monkeypatch.setattr("app.services.tenure.current_month", lambda: "2026-09")
    team = make_team(db)
    people = [make_person(db, str(1000 + i), team=team) for i in range(3)]
    for person in people:
        legacy_record(db, person, "2026-08", 100)
    for person in people:
        db.refresh(person)
    statements = []
    connection = db.connection()
    event.listen(connection, "before_cursor_execute", lambda *args: statements.append(args[2]))
    labels = tenure_labels(db, people)
    assert len(statements) == 3
    assert all(value == "2개월 (26/08 ~ )" for value in labels.values())
    for url in ("/people", f"/people/{people[0].id}", f"/teams/{team.id}"):
        page = auth_client.get(url)
        assert page.status_code == 200
        assert "재직 기간" in page.text and "2개월 (26/08 ~ )" in page.text


def monthly_event(db, person, month, at, action, before_status):
    from tests.test_history_reports import record

    status = {"new": "active", "returned": "active", "deactivated": "inactive"}[action]
    person.status = status
    operation = LedgerOperation(
        request_key=f"tenure-monthly-{person.id}-{month}",
        payload_hash="synthetic",
        kind="monthly",
        reason="합성 과거 월 확정",
        result_url="/monthly",
        created_at=at,
        detail_json=json.dumps(
            {
                "month": month,
                "changes": [
                    {
                        "point_no": person.point_no,
                        "action": action,
                        "before": {"status": before_status} if before_status else None,
                    }
                ],
            }
        ),
    )
    db.add(operation)
    db.flush()
    record(db, person, month, amount=100 if status == "active" else 0, at=at, op=operation)


def test_backdated_monthly_return_observes_actual_state_change_without_duplicate_period(client, db):
    person = make_person(db)
    legacy_record(db, person, "2026-06", 100)
    profile_event(db, person, datetime(2026, 9, 1, tzinfo=UTC), "active", "inactive")
    monthly_event(db, person, "2026-08", datetime(2026, 9, 9, tzinfo=UTC), "returned", "inactive")
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == "4개월 (26/06 ~ )"
    # 월간 확정 후 현재 포인트번호가 바뀌어도 당시 번호로 연결한 실제 변경을 잃지 않는다.
    person.point_no = "00123456"
    db.commit()
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == "4개월 (26/06 ~ )"


def test_later_profile_change_wins_over_backdated_monthly_event(client, db):
    person = make_person(db)
    legacy_record(db, person, "2026-06", 100)
    monthly_event(db, person, "2026-08", datetime(2026, 9, 1, tzinfo=UTC), "returned", "inactive")
    profile_event(db, person, datetime(2026, 9, 9, tzinfo=UTC), "active", "inactive")
    person.status = "inactive"
    db.commit()
    assert (
        tenure_labels(db, [person], through_month="2026-09")[person.id] == "3개월 (26/06 ~ 26/08)"
    )


def test_backdated_monthly_return_preserves_intervening_inactive_month_and_kst_boundary(client, db):
    person = make_person(db)
    legacy_record(db, person, "2026-06", 100)
    profile_event(db, person, datetime(2026, 7, 31, 15, tzinfo=UTC), "active", "inactive")
    monthly_event(
        db, person, "2026-07", datetime(2026, 8, 31, 15, tzinfo=UTC), "returned", "inactive"
    )
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "1개월 (26/09 ~ ), 2개월 (26/06 ~ 26/07)"
    )


def test_backdated_monthly_new_account_does_not_create_second_start(client, db):
    person = make_person(db)
    monthly_event(db, person, "2026-06", datetime(2026, 9, 1, tzinfo=UTC), "new", None)
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == "4개월 (26/06 ~ )"


def test_backdated_monthly_deactivation_wins_after_profile_return(client, db):
    person = make_person(db)
    legacy_record(db, person, "2026-06", 100)
    profile_event(db, person, datetime(2026, 9, 1, tzinfo=UTC), "inactive", "active")
    monthly_event(db, person, "2026-08", datetime(2026, 9, 9, tzinfo=UTC), "deactivated", "active")
    assert (
        tenure_labels(db, [person], through_month="2026-09")[person.id] == "2개월 (26/06 ~ 26/07)"
    )


def correct_month(db, person, month, amount, request_key):
    from sqlalchemy import select

    from app.models import AdminUser
    from app.services.ledger import apply_correction, correction_plan

    plan = correction_plan(
        db,
        person_id=person.id,
        month=month,
        carry="20",
        amount=str(amount),
        note="",
        reason="합성 기간 회귀 검증",
        request_key=request_key,
    )
    apply_correction(db, plan, actor_id=db.scalar(select(AdminUser.id)))
    db.commit()


def test_reused_number_past_insert_and_correction_do_not_reassign_monthly_history(
    auth_client, db, monkeypatch
):
    monkeypatch.setattr("app.services.tenure.current_month", lambda: "2026-09")
    team = make_team(db, "합성 기간팀")
    original = make_person(db, "2102", "합성 원래계정", team=team)
    legacy_record(db, original, "2026-06", 100)
    profile_event(db, original, datetime(2026, 7, 31, 15, tzinfo=UTC), "active", "inactive")
    monthly_event(
        db, original, "2026-07", datetime(2026, 8, 31, 15, tzinfo=UTC), "returned", "inactive"
    )
    previous_number = original.point_no
    original.point_no = "99002102"
    db.commit()
    other = make_person(
        db, "2103", "합성 별도계정", point_no=previous_number, status="inactive", team=team
    )
    correct_month(db, other, "2026-07", 0, "tenure-past-insert-000000000001")
    expected = "1개월 (26/09 ~ ), 2개월 (26/06 ~ 26/07)"

    def assert_consistent():
        assert tenure_labels(db, [original])[original.id] == expected
        assert tenure_labels(db, [original, other])[original.id] == expected
        assert tenure_labels(db, [other, original])[original.id] == expected
        for url in ("/people", f"/people/{original.id}", f"/teams/{team.id}"):
            response = auth_client.get(url)
            assert response.status_code == 200 and expected in response.text

    assert_consistent()
    # 뒤늦은 다른 계정 정정과 원래 기록의 새 revision도 당시 작업 연결을 바꾸지 않는다.
    correct_month(db, other, "2026-07", 50, "tenure-other-correct-000000000002")
    correct_month(db, original, "2026-07", 200, "tenure-original-correct-000000003")
    assert_consistent()


def test_legacy_correction_retains_completed_period_when_current_return_month_unknown(
    auth_client, db
):
    person = make_person(db, "2101", "합성 종료기간")
    legacy_record(db, person, "2026-06", 100)
    legacy_record(db, person, "2026-07", 0)
    legacy_record(db, person, "2026-08", 100)
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "2개월 (26/08 ~ ), 1개월 (26/06 ~ 26/06)"
    )
    correct_month(db, person, "2026-08", 0, "tenure-legacy-correct-00000000004")
    assert person.status == "active"
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "최근 재직 기간 미확인, 1개월 (26/06 ~ 26/06)"
    )


def test_unknown_current_departure_preserves_all_closed_periods_without_filling_gap(
    auth_client, db
):
    person = make_person(db, "2104", "합성 종료월 미확인", status="inactive")
    for month, amount in [
        ("2025-05", 100),
        ("2025-07", 0),
        ("2025-09", 100),
        ("2026-07", 0),
        ("2026-08", 100),
    ]:
        legacy_record(db, person, month, amount)
    assert tenure_labels(db, [person], through_month="2026-09")[person.id] == (
        "최근 재직 기간 미확인, 10개월 (25/09 ~ 26/06), 2개월 (25/05 ~ 25/06)"
    )
    from sqlalchemy import func, select

    from app.models import BalanceRecord, BalanceRevision, MonthlySnapshot

    before = (
        db.scalar(select(func.count(MonthlySnapshot.id))),
        db.scalar(select(func.count(BalanceRecord.id))),
        db.scalar(select(func.count(BalanceRevision.id))),
    )
    tenure_labels(db, [person], through_month="2026-09")
    after = (
        db.scalar(select(func.count(MonthlySnapshot.id))),
        db.scalar(select(func.count(BalanceRecord.id))),
        db.scalar(select(func.count(BalanceRevision.id))),
    )
    assert before == after == (5, 5, 5)
