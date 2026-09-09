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
        conflict.id: "미확인",
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
    assert len(statements) == 2
    assert all(value == "2개월 (26/08 ~ )" for value in labels.values())
    for url in ("/people", f"/people/{people[0].id}", f"/teams/{team.id}"):
        page = auth_client.get(url)
        assert page.status_code == 200
        assert "재직 기간" in page.text and "2개월 (26/08 ~ )" in page.text


def monthly_event(db, person, month, at, action, before_status):
    from tests.test_history_reports import record

    status = {"new": "active", "returned": "active", "deactivated": "inactive"}[action]
    person.status = status
    db.add(
        LedgerOperation(
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
    )
    record(db, person, month, amount=100 if status == "active" else 0, at=at)


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
