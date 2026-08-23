from app.models import BalanceRecord
from app.services import stats
from app.services.balance import create_monthly_snapshot
from tests.factories import make_person, make_team


def _confirm(client, month, rows, carries):
    data = {"month": month}
    for i, row in enumerate(rows):
        data[f"point_no_{i}"] = row[0]
        data[f"personal_no_{i}"] = row[1]
        data[f"name_{i}"] = row[2]
        data[f"team_{i}"] = row[3]
        data[f"grade_{i}"] = row[4]
        data[f"amount_{i}"] = str(row[5])
        data[f"carry_{i}"] = str(carries[row[0]])
    resp = client.post("/monthly/confirm", data=data, follow_redirects=False)
    assert resp.status_code == 303


def test_month_summary_empty(client, db):
    summary = stats.month_summary(db, "2026-07")
    assert summary.count == 0
    assert summary.total_balance == 0


def test_month_summary_with_records(client, db):
    person = make_person(db, "101", "김소방")
    create_monthly_snapshot(
        db,
        "2026-07",
        [
            BalanceRecord(
                person_id=person.id, carry_balance=1000, amount=50000, usage=2000, total=51000
            )
        ],
    )
    summary = stats.month_summary(db, "2026-07")
    assert summary.count == 1
    assert summary.total_amount == 50000
    assert summary.total_usage == 2000
    assert summary.total_balance == 51000


def test_month_summary_sums_signed_usage(client, db):
    person = make_person(db, "101", "김소방")
    create_monthly_snapshot(
        db,
        "2026-07",
        [
            BalanceRecord(
                person_id=person.id,
                carry_balance=3000,
                amount=1000,
                usage=-2000,
                total=4000,
            )
        ],
    )
    assert stats.month_summary(db, "2026-07").total_usage == -2000


def test_trend_orders_ascending(client, db):
    make_person(db, "101", "김소방")
    create_monthly_snapshot(
        db, "2026-06", [BalanceRecord(person_id=1, carry_balance=0, amount=0, usage=0, total=0)]
    )
    create_monthly_snapshot(
        db, "2026-07", [BalanceRecord(person_id=1, carry_balance=0, amount=0, usage=0, total=0)]
    )
    trend = stats.trend(db)
    assert [t.month for t in trend] == ["2026-06", "2026-07"]


def test_available_months_desc(client, db):
    make_person(db, "101", "김소방")
    create_monthly_snapshot(
        db, "2026-06", [BalanceRecord(person_id=1, carry_balance=0, amount=0, usage=0, total=0)]
    )
    create_monthly_snapshot(
        db, "2026-07", [BalanceRecord(person_id=1, carry_balance=0, amount=0, usage=0, total=0)]
    )
    assert stats.available_months(db) == ["2026-07", "2026-06"]


def test_team_summary_groups_by_team(client, db):
    team_a = make_team(db, "A팀", "#111111")
    team_b = make_team(db, "B팀", "#222222")
    p1 = make_person(db, "101", "김소방", team=team_a)
    p2 = make_person(db, "102", "이소방", team=team_a)
    p3 = make_person(db, "103", "박소방", team=team_b)
    p4 = make_person(db, "104", "최소방", team=None)
    create_monthly_snapshot(
        db,
        "2026-07",
        [
            BalanceRecord(person_id=p1.id, carry_balance=0, amount=1000, usage=0, total=1000),
            BalanceRecord(person_id=p2.id, carry_balance=0, amount=2000, usage=0, total=2000),
            BalanceRecord(person_id=p3.id, carry_balance=0, amount=3000, usage=0, total=3000),
            BalanceRecord(person_id=p4.id, carry_balance=0, amount=4000, usage=0, total=4000),
        ],
    )
    teams = stats.team_summary(db, "2026-07")
    by_name = {t.name: t for t in teams}
    assert by_name["A팀"].count == 2
    assert by_name["A팀"].total_amount == 3000
    assert by_name["B팀"].total_balance == 3000
    assert by_name["팀 없음"].total_amount == 4000


def test_team_summary_no_snapshot(client, db):
    assert stats.team_summary(db, "2026-07") == []


def test_person_summary_ordered_by_name(client, db):
    p1 = make_person(db, "101", "가나다")
    p2 = make_person(db, "102", "김소방")
    create_monthly_snapshot(
        db,
        "2026-07",
        [
            BalanceRecord(person_id=p1.id, carry_balance=0, amount=1000, usage=0, total=1000),
            BalanceRecord(person_id=p2.id, carry_balance=0, amount=2000, usage=0, total=2000),
        ],
    )
    persons = stats.person_summary(db, "2026-07")
    assert [p.name for p in persons] == ["가나다", "김소방"]
    assert persons[1].total == 2000


def test_person_summary_no_snapshot(client, db):
    assert stats.person_summary(db, "2026-07") == []


def test_person_summary_excludes_shared_accounts(client, db):
    person = make_person(db, "101", "일반")
    shared = make_person(
        db,
        "",
        "공용",
        point_no="00000009",
        account_type="shared",
    )
    create_monthly_snapshot(
        db,
        "2026-07",
        [
            BalanceRecord(person_id=person.id, carry_balance=0, amount=1000, usage=0, total=1000),
            BalanceRecord(person_id=shared.id, carry_balance=0, amount=9000, usage=0, total=9000),
        ],
    )

    assert [item.name for item in stats.person_summary(db, "2026-07")] == ["일반"]


def test_dashboard_empty_state(auth_client):
    resp = auth_client.get("/dashboard")
    assert resp.status_code == 200
    assert "아직 처리된 월간 데이터가 없습니다" in resp.text


def test_dashboard_with_data(auth_client, db):
    _confirm(
        auth_client,
        "2026-07",
        [("00000001", "101", "김소방", "1팀", "소방경", 50000)],
        {"00000001": 10000},
    )
    resp = auth_client.get("/dashboard")
    assert resp.status_code == 200
    assert "2026-07" in resp.text
    assert "60,000원" in resp.text
    assert "1명" in resp.text
    assert "김소방" in resp.text
    assert "1팀" in resp.text
    assert "trend-chart" in resp.text


def test_dashboard_month_select(auth_client, db):
    _confirm(
        auth_client,
        "2026-06",
        [("00000001", "101", "김소방", "1팀", "소방경", 50000)],
        {"00000001": 10000},
    )
    _confirm(
        auth_client,
        "2026-07",
        [("00000001", "101", "김소방", "1팀", "소방경", 60000)],
        {"00000001": 5000},
    )
    resp = auth_client.get("/dashboard?month=2026-06")
    assert "65,000원" not in resp.text
    resp = auth_client.get("/dashboard?month=2026-07")
    assert "65,000원" in resp.text


def test_dashboard_invalid_month_falls_back(auth_client, db):
    _confirm(
        auth_client,
        "2026-07",
        [("00000001", "101", "김소방", "1팀", "소방경", 50000)],
        {"00000001": 10000},
    )
    resp = auth_client.get("/dashboard?month=2099-01")
    assert resp.status_code == 200
    assert "2026-07" in resp.text


def test_dashboard_requires_login(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303
