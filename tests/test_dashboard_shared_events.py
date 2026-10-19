"""대시보드가 한 번 읽은 관측을 보고서와 추이에 나눠 써도 개별 조회와 같은 집계다."""

import json
from dataclasses import astuple

import pytest

from app.models import LedgerOperation
from app.services import stats
from app.services.history import preserve_revision
from app.services.observations import observation_events
from tests.factories import make_person, make_team
from tests.test_history_reports import adjustment, moment, operation, record


def _view(report):
    return (
        report.rows,
        report.summary,
        report.teams,
        report.unclassified_rows,
        report.active_teams,
        report.holding_teams,
        report.holding_unclassified_rows,
        report.team_status_unknown_count,
    )


@pytest.fixture()
def ledger(client, db):
    team_a, team_b = make_team(db, "합성가팀"), make_team(db, "합성나팀", color="#123456")
    kept = make_person(db, "101", "합성유지", team=team_a)
    returned = make_person(db, "102", "합성복귀", team=team_b)
    shared = make_person(db, "", "합성공용", point_no="00000103", account_type="shared")
    adjusted = make_person(db, "104", "합성보정", team=team_a)
    make_person(db, "105", "합성미관측", team=team_b)
    unclassified = make_person(db, "106", "합성미분류")
    first = record(db, kept, "2026-01", amount=100)
    record(db, kept, "2026-03", carry=70, amount=50, usage=30)
    record(db, returned, "2026-01", amount=0, provenance="master_at_migration")
    record(db, returned, "2026-03", amount=200, provenance="master_at_migration")
    record(db, shared, "2026-02", amount=300, at=moment(2026, 2, 1))
    record(db, unclassified, "2026-03", profile={"name": "합성미분류", "account_type": ""})
    adjustment(db, adjusted, "2026-02", 500, moment(2026, 2, 3), op=operation(db, "adjust-1"))
    correction = operation(db, "shared-events-correction")
    first.amount, first.total, first.version = 110, 110, 2
    preserve_revision(db, first, correction.id, source="correction")
    adjustment(db, shared, "2026-03", 250, moment(2026, 3, 5), op=operation(db, "adjust-2"))
    changes = [
        {"point_no": "00000101", "action": "kept"},
        {"point_no": "00000102", "action": "returned"},
    ]
    db.add(
        LedgerOperation(
            request_key="shared-events-monthly",
            payload_hash="m" * 64,
            kind="monthly",
            reason="합성 월간",
            detail_json=json.dumps({"month": "2026-03", "changes": changes}),
            result_url="/monthly",
        )
    )
    db.commit()
    return [0, correction.id, stats.report_cutoff(db)]


def test_report_and_trend_match_with_shared_events(client, db, ledger):
    months = stats.available_months(db)
    assert months == ["2026-03", "2026-02", "2026-01"]
    # 비교 대상이 빈 보고서가 아니라 정정판·미관측·공용·보정·미분류를 실제로 포함하는지 먼저 본다.
    base, latest = ledger[0], ledger[-1]
    assert (
        stats.report(db, "2026-01", "observed", "all", operation_id=base).summary.total_amount
        == 100
    )
    assert (
        stats.report(db, "2026-01", "observed", "all", operation_id=latest).summary.total_amount
        == 110
    )
    march = stats.report(db, "2026-03", "as_of", "all", operation_id=latest)
    assert {row.name for row in march.rows} >= {"합성유지", "합성복귀", "합성공용", "합성보정"}
    assert any(row.balance_kind == "unobserved" for row in march.rows)
    assert stats.report(db, "2026-03", "observed", "person", operation_id=latest).unclassified_rows
    compared = 0
    for cutoff in ledger:
        events = observation_events(db, operation_id=cutoff)
        for scope in ("observed", "as_of"):
            for account_type in ("person", "shared", "all"):
                for team_name in (None, "합성가팀"):
                    filters = {"scope": scope, "account_type": account_type}
                    expected = stats.trend(db, **filters, operation_id=cutoff, team_name=team_name)
                    actual = stats.trend(
                        db, **filters, operation_id=cutoff, team_name=team_name, all_events=events
                    )
                    assert [astuple(s) for s in actual] == [astuple(s) for s in expected]
                    for month in months:
                        args = (db, month, scope, account_type, None, team_name, cutoff)
                        assert _view(stats.report(*args, all_events=events)) == _view(
                            stats.report(*args)
                        )
                        compared += 1
    assert compared == 3 * 2 * 3 * 2 * 3


def test_dashboard_reads_events_once_for_report_and_trend(auth_client, db, ledger, monkeypatch):
    from app.routers import dashboard as dashboard_router

    calls = []
    original = dashboard_router.observation_events

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(dashboard_router, "observation_events", counted)
    monkeypatch.setattr(stats, "observation_events", counted)
    response = auth_client.get("/dashboard?month=2026-03&account_type=all")
    assert response.status_code == 200
    assert calls == [{"operation_id": ledger[-1]}]
