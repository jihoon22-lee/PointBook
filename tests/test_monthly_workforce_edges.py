"""개별 등록·복귀 → 월간 확정과 누락 월 추가의 업무 경계 회귀."""

import io
import re
from contextlib import closing

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.models import BalanceAdjustment, BalanceRecord, Person
from app.services import stats
from app.services.dates import current_month
from app.services.history import profile_for_record
from tests.factories import make_person
from tests.monthly_helpers import review_fields
from tests.test_legacy_status import legacy_record
from tests.test_profiles import _apply, _edit_form, _new_form, form_values


def assert_workforce(client, db, month, active, departed):
    for scope in ("observed", "as_of"):
        result = stats.report(db, month, scope, account_type="all")
        assert (result.summary.active_count, result.summary.deactivated_count) == (active, departed)
        assert result.summary.workforce_count == active + departed
        assert sum(team.workforce_count for team in result.teams) == active + departed
        summary = next(
            s for s in stats.trend(db, scope=scope, account_type="all") if s.month == month
        )
        assert (summary.active_count, summary.deactivated_count) == (active, departed)
    counts = dict(
        re.findall(
            r'<td class="month-label">(\d{4}-\d{2}).*?</td>\s*<td>(\d+)명</td>',
            client.get("/monthly").text,
            re.DOTALL,
        )
    )
    assert counts[month] == str(active + departed)
    page = client.get(f"/dashboard?month={month}&account_type=all")
    assert f"재직 {active}명 · 비재직 전환 {departed}명" in page.text
    response = client.get(f"/dashboard/export.xlsx?month={month}&account_type=all")
    with closing(load_workbook(io.BytesIO(response.content))) as workbook:
        summary = {row[0].value: row[1].value for row in workbook["요약"]}
        assert summary["월간 재직 인원"] == active
        assert summary["월간 비재직 전환 인원"] == departed
        assert summary["월간 처리 인원"] == active + departed


@pytest.mark.parametrize("previously_inactive", [False, True])
def test_individual_registration_or_return_then_monthly_departure(
    auth_client, db, previously_inactive
):
    if previously_inactive:
        missing = make_person(db, "101", "합성누락", status="inactive")
        legacy_record(db, missing, "2025-01", 0)
        path = f"/people/{missing.id}/edit"
        values = _edit_form(auth_client, missing, status="active")
    else:
        path = "/people/new"
        values = _new_form(
            auth_client,
            point_no="00000101",
            personal_no="101",
            name="합성누락",
            carry_balance="0",
            amount="0",
        )
    preview = auth_client.post(path, data=values)
    assert preview.status_code == 200
    assert _apply(auth_client, path, values, preview).status_code == 303
    db.expire_all()
    missing = db.scalar(select(Person).where(Person.point_no == "00000101"))
    assert missing.status == "active"
    if not previously_inactive:
        assert db.scalar(select(BalanceAdjustment).where(BalanceAdjustment.person_id == missing.id))
        assert not db.scalar(select(BalanceRecord).where(BalanceRecord.person_id == missing.id))
    retained = make_person(db, "103", "합성기존비재직", status="inactive")
    legacy_record(db, retained, "2025-01", 0, carry=70)
    month = current_month()
    reviewed = auth_client.post(
        "/monthly/review",
        data={
            "month": month,
            "point_no_0": "00000102",
            "account_type_0": "person",
            "personal_no_0": "102",
            "name_0": "합성재직",
            "team_0": "",
            "grade_0": "",
            "amount_0": "100",
            "carry_0": "0",
            "point_no_1": "00000104",
            "account_type_1": "shared",
            "personal_no_1": "",
            "name_1": "합성공용",
            "team_1": "",
            "grade_1": "",
            "amount_1": "0",
            "carry_1": "80",
        },
    )
    values = review_fields(reviewed)
    values.update(
        deactivated_carry_00000101="0", deactivated_carry_00000103="70", ack_warnings="yes"
    )
    confirmed = auth_client.post("/monthly/confirm", data=values, follow_redirects=False)
    assert confirmed.status_code == 303, confirmed.text
    db.expire_all()
    assert missing.status == "inactive" and retained.status == "inactive"
    assert_workforce(auth_client, db, month, 1, 1)
    assert stats.report(db, month, scope="as_of", account_type="all").summary.total_balance == 250
    # 이후 번호를 고쳐도 고정된 확정 기록과 연결하며 cutoff 이전에 활동을 만들지 않는다.
    values = _edit_form(auth_client, missing, point_no="00000901")
    path = f"/people/{missing.id}/edit"
    preview = auth_client.post(path, data=values)
    assert _apply(auth_client, path, values, preview, confirm_identity="yes").status_code == 303
    db.expire_all()
    assert_workforce(auth_client, db, month, 1, 1)
    assert stats.report(db, month, operation_id=0).summary.workforce_count == 0


@pytest.mark.parametrize(
    "account_type,amount,expected_status",
    [
        ("person", 50, "active"),
        ("person", 0, "inactive"),
        ("shared", 0, "active"),
    ],
)
def test_missing_month_http_correction_uses_payment_and_preserves_current_state(
    auth_client,
    db,
    account_type,
    amount,
    expected_status,
):
    current_status = "inactive" if amount else "active"
    person = make_person(
        db,
        "105" if account_type == "person" else "",
        "합성과거",
        point_no="00000105",
        status=current_status,
        account_type=account_type,
    )
    first = legacy_record(db, person, "2025-01", 100)
    following = legacy_record(db, person, "2025-03", 0, carry=100)
    person.current_carry_balance = 100
    db.commit()
    original = first.profile_data
    path = f"/ledger/correct/{person.id}"
    values = {
        **form_values(auth_client.get(f"{path}?month=2025-02")),
        "carry": "100",
        "amount": str(amount),
        "reason": "누락 월 원본 대조",
        "intent": "preview",
    }
    preview = auth_client.post(f"{path}/preview", data=values)
    assert preview.status_code == 200
    response = auth_client.post(
        f"{path}/apply",
        data={**values, "plan_token": form_values(preview)["plan_token"]},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    db.expire_all()
    report = stats.report(db, "2025-02", account_type="all")
    assert report.rows[0].status == expected_status
    assert_workforce(
        auth_client,
        db,
        "2025-02",
        int(account_type == "person" and amount > 0),
        int(account_type == "person" and amount == 0),
    )
    assert person.status == current_status
    assert person.current_carry_balance == 100 and person.current_amount == 0
    assert following.usage == amount
    assert (following.carry_balance, following.amount, following.total) == (100, 0, 100)
    assert first.profile_data == original
    inserted = db.scalar(
        select(BalanceRecord).where(
            BalanceRecord.person_id == person.id, BalanceRecord.id.not_in([first.id, following.id])
        )
    )
    assert profile_for_record(inserted)["status"] == expected_status
    assert stats.report(db, "2025-02", operation_id=0).rows == []
    # 삽입한 월을 다시 정정해도 선택한 정정판의 지급액에 같은 판정을 적용한다.
    cutoff = stats.report_cutoff(db)
    values = {
        **form_values(auth_client.get(f"{path}?month=2025-02")),
        "amount": str(0 if amount else 50),
        "reason": "지급액 재대조",
        "intent": "preview",
    }
    preview = auth_client.post(f"{path}/preview", data=values)
    assert (
        auth_client.post(
            f"{path}/apply",
            data={**values, "plan_token": form_values(preview)["plan_token"]},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        stats.report(db, "2025-02", account_type="all", operation_id=cutoff).rows[0].status
        == expected_status
    )
    expected_after = "active" if account_type == "shared" or not amount else "inactive"
    assert stats.report(db, "2025-02", account_type="all").rows[0].status == expected_after
