import json
import time
from datetime import UTC, datetime

from sqlalchemy import event, select

from app.models import BalanceAdjustment, BalanceRecord, LedgerOperation, MonthlySnapshot
from app.services import stats
from app.services.history import preserve_revision, profile_for_person
from app.services.observations import latest_observations, observation_events
from tests.factories import make_person, make_team


def moment(year, month, day):
    # Models store naive UTC values in SQLite.
    return datetime(year, month, day, tzinfo=UTC).replace(tzinfo=None)


def operation(db, key="operation"):
    op = LedgerOperation(
        request_key=key,
        payload_hash="x" * 64,
        kind="correction",
        reason="합성 검증",
        result_url="/dashboard",
    )
    db.add(op)
    db.flush()
    return op


def record(
    db,
    person,
    month,
    *,
    carry=0,
    amount=100,
    usage=0,
    at=None,
    provenance="observed",
    op=None,
    note="합성 비고",
    profile=None,
):
    snapshot = db.scalar(select(MonthlySnapshot).where(MonthlySnapshot.month == month))
    if snapshot is None:
        snapshot = MonthlySnapshot(month=month)
        db.add(snapshot)
        db.flush()
    row = BalanceRecord(
        snapshot_id=snapshot.id,
        person_id=person.id,
        carry_balance=carry,
        amount=amount,
        usage=usage,
        total=carry + amount,
        observed_at=at,
        profile_data=json.dumps(profile_for_person(person) if profile is None else profile),
        provenance=provenance,
        note=note,
        version=1,
    )
    db.add(row)
    preserve_revision(db, row, op.id if op else None, source="test_baseline")
    db.commit()
    return row


def adjustment(db, person, month, total, at, *, op=None):
    op = op or operation(db)
    row = BalanceAdjustment(
        person_id=person.id,
        month=month,
        total=total,
        observed_at=at,
        profile_data=json.dumps(profile_for_person(person)),
        operation_id=op.id,
        note="합성 보정",
    )
    db.add(row)
    db.commit()
    return row


def test_latest_observation_uses_month_then_actual_timestamp(client, db):
    person = make_person(db, "101", "합성 인원")
    record(db, person, "2026-01", amount=100, at=None)
    adjustment(db, person, "2026-01", 200, moment(2026, 1, 2))
    record(db, person, "2026-03", carry=150, amount=50, usage=50, at=moment(2026, 3, 5))
    assert latest_observations(db)[person.id].month == "2026-03"
    january = latest_observations(db, through_month="2026-02")[person.id]
    assert january.total == 200
    assert january.kind == "adjustment"
    assert january.usage is None
    assert latest_observations(db, before_month="2026-03")[person.id].total == 200
    assert not latest_observations(db, before_month="2026-01")


def test_same_month_adjustment_before_monthly_is_previous_balance(client, db):
    person = make_person(db, "101", "합성 인원")
    adjustment(db, person, "2026-03", 100, moment(2026, 3, 1))
    row = record(db, person, "2026-03", amount=200, at=moment(2026, 3, 2))
    latest = latest_observations(db)[person.id]
    assert latest.record_id == row.id
    assert latest.total == 200
    prior = latest_observations(db, before_month="2026-03", before_at=moment(2026, 3, 2))
    assert prior[person.id].total == 100
    assert not latest_observations(db, before_month="2026-03", before_at=moment(2026, 3, 1))
    assert not latest_observations(db, person_ids=[])


def test_record_amounts_and_status_survive_while_identity_team_and_grade_use_current_master(
    client, db
):
    old_team = make_team(db, "합성 옛팀")
    new_team = make_team(db, "합성 새팀")
    person = make_person(db, "101", "합성 옛이름", team=old_team)
    record(db, person, "2026-01", carry=10, amount=100, usage=-20)
    person.name = "합성 새이름"
    person.grade = "최신 계급"
    person.team = new_team
    person.account_type = "shared"
    person.personal_no = None
    person.status = "active"
    db.commit()
    report = stats.report(db, "2026-01")
    assert report.rows[0].name == "합성 새이름"
    assert report.rows[0].team_name == "합성 새팀"
    assert report.rows[0].grade == "최신 계급"
    assert report.rows[0].account_type == "person"
    assert report.rows[0].usage == -20
    assert not stats.report(db, "2026-01", account_type="shared").rows
    assert report.summary.total_balance == 110
    assert report.teams[0].name == "합성 새팀"


def test_as_of_retains_long_unobserved_inactive_and_shared_balances(client, db):
    person = make_person(db, "101", "합성 일반", status="inactive")
    shared = make_person(db, "", "합성 공용", point_no="00000002", account_type="shared")
    unknown = make_person(db, "103", "합성 미관측")
    unknown.current_amount = 900
    db.commit()
    record(db, person, "2025-01", amount=100)
    record(db, shared, "2025-02", amount=200)
    result = stats.report(db, "2026-12", scope="as_of", account_type="all")
    assert result.summary.count == 3
    assert result.summary.total_balance == 300
    assert result.summary.total_amount == result.summary.total_usage == 0
    assert result.summary.processed_count == 0
    assert result.summary.unknown_count == 1
    by_id = {r.person_id: r for r in result.rows}
    assert by_id[person.id].observation_month == "2025-01"
    assert by_id[shared.id].observation_month == "2025-02"
    assert by_id[unknown.id].total is None
    assert by_id[unknown.id].usage is None
    assert by_id[unknown.id].current_reference_total == 900
    assert "현재정보 참고" in by_id[unknown.id].profile_label
    assert not stats.report(db, "2026-12", scope="observed", account_type="all").rows
    assert stats.report(db, "2026-12", scope="as_of").summary.total_balance == 100
    assert (
        stats.report(db, "2026-12", scope="as_of", account_type="shared").summary.total_balance
        == 200
    )


def test_adjustment_balance_does_not_erase_actual_monthly_activity(client, db):
    person = make_person(db, "101", "합성 인원")
    record(db, person, "2026-01", amount=100, carry=20, usage=-10, at=moment(2026, 1, 1))
    adjustment(db, person, "2026-01", 90, moment(2026, 1, 3))
    result = stats.report(db, "2026-01")
    assert result.summary.total_amount == 100
    assert result.summary.total_usage == -10
    assert result.summary.total_balance == 90
    assert result.rows[0].balance_kind == "adjustment"
    assert result.rows[0].amount == 100
    assert result.rows[0].activity_note == "합성 비고"
    assert result.rows[0].note == "합성 보정"


def test_same_month_type_team_change_keeps_activity_and_balance_populations_distinct(client, db):
    team_a, team_b = make_team(db, "합성 A"), make_team(db, "합성 B")
    person = make_person(db, "101", "합성 인원", team=team_a)
    record(db, person, "2026-01", amount=100, at=moment(2026, 1, 1))
    person.account_type, person.team = "shared", team_b
    db.commit()
    adjustment(db, person, "2026-01", 200, moment(2026, 1, 3))
    ordinary = stats.report(db, "2026-01", account_type="person")
    shared = stats.report(db, "2026-01", account_type="shared")
    all_accounts = stats.report(db, "2026-01", account_type="all")
    assert ordinary.summary.total_amount == 100
    assert ordinary.summary.total_balance == 0
    assert ordinary.rows[0].balance_kind == "activity_only"
    assert shared.summary.total_amount == 0
    assert shared.summary.total_balance == 200
    assert all_accounts.summary.total_amount == 100
    assert all_accounts.summary.total_balance == 200
    teams = {t.name: t for t in all_accounts.teams}
    assert "합성 A" not in teams
    assert teams["합성 B"].total_amount == 100
    assert teams["합성 B"].total_balance == 200


def test_report_replays_immutable_revision_at_operation_cutoff(client, db):
    person = make_person(db, "101", "합성 인원")
    row = record(db, person, "2026-01", amount=100)
    op1 = operation(db, "correction-1")
    row.amount, row.total, row.version = 150, 150, 2
    preserve_revision(db, row, op1.id, source="correction")
    db.commit()
    op2 = operation(db, "correction-2")
    row.amount, row.total, row.version = 200, 200, 3
    preserve_revision(db, row, op2.id, source="correction")
    db.commit()
    assert stats.report(db, "2026-01", operation_id=0).summary.total_amount == 100
    assert stats.report(db, "2026-01", operation_id=op1.id).summary.total_amount == 150
    assert stats.report(db, "2026-01", operation_id=op2.id).summary.total_amount == 200
    assert latest_observations(db, operation_id=op1.id)[person.id].version == 2
    adjustment(db, person, "2026-02", 300, moment(2026, 2, 2), op=op2)
    assert latest_observations(db, operation_id=op1.id)[person.id].month == "2026-01"
    assert latest_observations(db, operation_id=op2.id)[person.id].kind == "adjustment"


def test_later_inserted_month_does_not_appear_in_earlier_revision(client, db):
    person = make_person(db, "101", "합성 인원")
    op = operation(db, "new-record")
    record(db, person, "2026-01", op=op)
    assert not observation_events(db, operation_id=0)
    assert not stats.report(db, "2026-01", operation_id=0).rows
    assert stats.report(db, "2026-01", operation_id=op.id).rows


def test_migration_reference_is_frozen_and_never_claimed_as_observed(client, db):
    person = make_person(db, "101", "이관 참고 이름")
    record(db, person, "2020-01", provenance="master_at_migration")
    person.name = "바뀐 현재 이름"
    db.commit()
    row = stats.report(db, "2020-01").rows[0]
    assert row.name == "바뀐 현재 이름"
    assert row.profile_label == "기존 장부"
    assert row.observed_at is None


def test_unclassified_history_is_exposed_without_current_master_reclassification(client, db):
    person = make_person(db, "101", "현재 이름")
    record(db, person, "2020-01", profile={}, provenance="unknown")
    result = stats.report(db, "2020-01")
    assert not result.rows
    assert result.unclassified_rows[0].total == 100
    assert result.unclassified_rows[0].name == "현재 이름"
    assert stats.report(db, "2020-01", account_type="all").summary.total_balance == 100


def test_observation_and_trend_queries_are_bounded_as_months_grow(client, db):
    person = make_person(db, "101", "합성 인원")
    for year in range(2020, 2025):
        for month in range(1, 13):
            record(db, person, f"{year}-{month:02d}")
    person_id = person.id
    calls = []
    engine = db.get_bind()

    def count_queries(conn, cursor, statement, parameters, context, many):
        calls.append(statement)

    event.listen(engine, "before_cursor_execute", count_queries)
    started = time.monotonic()
    try:
        assert latest_observations(db)[person_id].month == "2024-12"
        assert len(calls) == 1
        calls.clear()
        assert len(stats.trend(db)) == 60
        assert len(calls) == 4  # 월간 확정 전환 기록을 배치 조회하는 상수 1회 포함
        calls.clear()
        assert stats.report(db, "2024-12", scope="as_of").summary.total_balance == 100
        assert len(calls) == 5  # 인원·기간에 비례하지 않는 월간 확정 배치 조회 포함
        assert time.monotonic() - started < 3
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)


def test_same_timestamp_adjustments_have_deterministic_id_order(client, db):
    person = make_person(db, "101", "합성 인원")
    op = operation(db, "same-time")
    first = adjustment(db, person, "2026-01", 100, moment(2026, 1, 1), op=op)
    second = adjustment(db, person, "2026-01", 200, moment(2026, 1, 1), op=op)
    result = latest_observations(db)[person.id]
    assert second.id > first.id
    assert result.adjustment_id == second.id
    assert result.total == 200


def test_report_filters_activity_and_balances_using_current_team(client, db):
    team = make_team(db, "역사 팀")
    first = make_person(db, "101", "합성 첫째", team=team)
    second = make_person(db, "102", "합성 둘째")
    record(db, first, "2026-01", amount=100)
    record(db, second, "2026-01", amount=200)
    first.team = None
    db.commit()
    filtered = stats.report(db, "2026-01", team_name="역사 팀")
    assert filtered.rows == []
    assert stats.trend(db, team_name="역사 팀")[0].total_amount == 0
    assert stats.report(db, "2026-01", team_name="").summary.total_amount == 300
    assert stats.trend(db, person_id=second.id)[0].total_amount == 200


def test_dashboard_captures_one_revision_cutoff_for_summary_and_chart(auth_client, db, monkeypatch):
    person = make_person(db, "101", "합성 인원")
    row = record(db, person, "2026-01", amount=100)
    original = stats.report_cutoff
    calls = []

    def capture_then_correct(session):
        cutoff = original(session)
        calls.append(cutoff)
        op = operation(db, "concurrent-correction")
        row.amount, row.total, row.version = 900, 900, 2
        preserve_revision(db, row, op.id, source="correction")
        db.commit()
        return cutoff

    monkeypatch.setattr(stats, "report_cutoff", capture_then_correct)
    response = auth_client.get("/dashboard?month=2026-01&scope=as_of")
    assert response.status_code == 200
    assert calls == [0]
    assert '"amount": [100]' in response.text
    assert '"balance": [100]' in response.text
    assert "기준 작업" not in response.text
    assert "900원" not in response.text


def test_dashboard_exposes_shared_unobserved_and_revision_labels(auth_client, db):
    shared = make_person(db, "", "합성 공용", point_no="00000001", account_type="shared")
    record(db, shared, "2025-01", amount=300, provenance="master_at_migration")
    unknown = make_person(db, "", "합성 미관측공용", point_no="00000002", account_type="shared")
    unknown.current_amount = 700
    db.commit()
    response = auth_client.get(
        "/dashboard?month=2026-01&scope=as_of&account_type=shared&operation_id=0"
    )
    assert response.status_code == 200
    for text in [
        "합성 공용",
        "합성 미관측공용",
        "2025-01",
        "현재값 참고: 700원",
        "팀·계급과 팀별 집계는 현재 인원 정보 기준",
        "변경 이력에서 선택한 시점의 금액",
        "부분 합계",
    ]:
        assert text in response.text
    assert "300원" in response.text
    assert "1,000원" not in response.text


def test_dashboard_rejects_invalid_report_filters(auth_client):
    for query in [
        "month=2026-13",
        "month=0000-01",
        "scope=invalid",
        "account_type=invalid",
        "operation_id=-1",
    ]:
        assert auth_client.get("/dashboard?" + query).status_code == 400


import pytest


@pytest.mark.parametrize("population", [500, 2000])
def test_report_scale_keeps_query_count_and_uses_python_integer_totals(client, db, population):
    from sqlalchemy import insert

    from app.models import BalanceRevision, Person

    people = [
        Person(
            point_no=f"{i:08d}",
            personal_no="합성 중복",
            name=f"합성 계정{i:04d}",
            account_type="shared" if i % 5 == 0 else "person",
            status="active" if i % 5 == 0 or i % 4 else "inactive",
            current_amount=100,
        )
        for i in range(1, population + 1)
    ]
    snapshots = [MonthlySnapshot(month=f"2025-{month:02d}") for month in range(1, 13)]
    db.add_all([*people, *snapshots])
    db.flush()
    profiles = {p.id: profile_for_person(p) for p in people}
    months = {snapshot.id: snapshot.month for snapshot in snapshots}
    mappings = [
        {
            "person_id": p.id,
            "snapshot_id": snapshot.id,
            "amount": 100,
            "carry_balance": 0,
            "total": 100,
            "usage": 0 if snapshot.month.endswith("01") else 100,
            "profile_data": json.dumps(profiles[p.id]),
            "provenance": "observed",
            "version": 1,
        }
        for snapshot in snapshots
        for p in people
    ]
    inserted = db.execute(
        insert(BalanceRecord).returning(
            BalanceRecord.id, BalanceRecord.person_id, BalanceRecord.snapshot_id
        ),
        mappings,
    ).all()
    revisions = [
        {
            "record_id": rid,
            "version": 1,
            "operation_id": None,
            "source": "test_baseline",
            "data_json": json.dumps(
                {
                    "month": months[sid],
                    "profile": profiles[pid],
                    "amount": 100,
                    "carry_balance": 0,
                    "total": 100,
                    "usage": 0 if months[sid].endswith("01") else 100,
                    "provenance": "observed",
                    "version": 1,
                    "observed_at": None,
                    "note": "",
                }
            ),
        }
        for rid, pid, sid in inserted
    ]
    db.execute(insert(BalanceRevision), revisions)
    db.commit()
    queries = []
    engine = db.get_bind()

    def count_queries(conn, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", count_queries)
    started = time.monotonic()
    try:
        assert len(latest_observations(db)) == population
        assert len(queries) == 1
        queries.clear()
        result = stats.report(db, "2025-12", scope="as_of", account_type="all")
        assert result.summary.total_balance == population * 100
        assert result.summary.count == population
        assert len(queries) == 5  # 월간 확정 전환 기록을 배치 조회하는 상수 1회 포함
        queries.clear()
        assert len(stats.trend(db, scope="as_of", account_type="all")) == 12
        assert len(queries) == 4  # 월 수와 무관하게 확정 기록을 한 번에 조회
        elapsed = time.monotonic() - started
        assert elapsed < 5
        print(
            f"synthetic population={population} months=12 query-counts=1/4/3 elapsed={elapsed:.3f}s"
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)


def test_dashboard_filter_form_accepts_blank_latest_revision(auth_client, db):
    person = make_person(db, "101", "합성 인원")
    record(db, person, "2026-01")
    response = auth_client.get(
        "/dashboard?month=2026-01&scope=as_of&account_type=person&team_name=&operation_id="
    )
    assert response.status_code == 200
    assert "정정판 작업 번호" not in response.text


def test_unknown_balance_still_visible_when_monthly_activity_is_classified(client, db):
    person = make_person(db, "101", "합성 인원")
    record(db, person, "2026-01", amount=100)
    op = operation(db, "unknown-profile")
    unknown = BalanceAdjustment(
        person_id=person.id,
        month="2026-01",
        total=90,
        observed_at=moment(2026, 1, 2),
        profile_data="{}",
        operation_id=op.id,
        note="이름과 유형 미확인",
    )
    db.add(unknown)
    db.commit()
    result = stats.report(db, "2026-01")
    assert result.summary.total_amount == 100
    assert result.summary.total_balance == 0
    assert result.rows[0].balance_kind == "activity_only"
    assert result.unclassified_rows[0].total == 90


def test_missing_old_team_does_not_override_current_team_filter(client, db):
    person = make_person(db, "101", "합성 인원")
    record(db, person, "2026-01", profile={"name": "합성 당시 이름", "account_type": "person"})
    result = stats.report(db, "2026-01", account_type="all", team_name="합성 팀")
    assert not result.rows
    assert result.unclassified_rows == []
    assert stats.report(db, "2026-01", team_name="").summary.total_balance == 100


def test_correction_reference_profile_is_marked_as_unverified_history(client, db):
    person = make_person(db, "101", "정정 당시 참고 이름")
    record(db, person, "2020-01", provenance="reference_at_correction")
    row = stats.report(db, "2020-01").rows[0]
    assert row.name == "정정 당시 참고 이름"
    assert row.profile_label == "정정 당시 정보 참고 · 당시 사실 미확인"


def test_revision_and_live_queries_share_timestamp_precision_and_tie_order(client, db):
    other = make_person(db, "101", "합성 첫째")
    person = make_person(db, "102", "합성 둘째")
    record(db, other, "2026-01", at=moment(2026, 1, 1))
    monthly = record(db, person, "2026-01", amount=300, at=moment(2026, 1, 2))
    op = operation(db, "equal-timestamp")
    changed = adjustment(db, person, "2026-01", 200, moment(2026, 1, 2), op=op)
    assert monthly.id > changed.id
    live = latest_observations(db)[person.id]
    revision = latest_observations(db, operation_id=op.id)[person.id]
    assert live.record_id == monthly.id
    assert revision.record_id == live.record_id
    assert revision.observed_at == live.observed_at


@pytest.mark.parametrize(
    "legacy_time",
    ["2026-01-02T00:00:00.1", "2026-01-02 00:00:00.100", "2026-01-02T00:00:00.100000"],
)
def test_legacy_fractional_timestamp_precision_is_normalized(client, db, monkeypatch, legacy_time):
    from app.services import history

    original = history.record_data

    def legacy_data(row, month=None):
        value = original(row, month)
        value["observed_at"] = legacy_time
        return value

    monkeypatch.setattr(history, "record_data", legacy_data)
    other = make_person(db, "101", "합성 첫째")
    person = make_person(db, "102", "합성 둘째")
    at = moment(2026, 1, 2).replace(microsecond=100000)
    record(db, other, "2026-01", at=at)
    monthly = record(db, person, "2026-01", at=at)
    op = operation(db, "fractional-timestamp")
    adjustment(db, person, "2026-01", 200, at, op=op)
    replayed = latest_observations(db, operation_id=op.id)[person.id]
    assert replayed.record_id == monthly.id
    assert replayed.observed_at == at


def test_adjustment_only_month_is_not_shown_as_zero_usage_observation(auth_client, db):
    person = make_person(db, "101", "합성 인원")
    adjustment(db, person, "2026-01", 100, moment(2026, 1, 2))
    response = auth_client.get("/dashboard?month=2026-01")
    assert response.status_code == 200
    assert "월간 기록 없음" in response.text
    assert '"amount": [null]' in response.text
    assert '"usage": [null]' in response.text
    assert '"balance": [100]' in response.text
