"""화면 사용성: 한국 시간·사람이 읽는 정합성 이슈·반영 배너·월간 요약·팀 선택·운영 카드."""

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import BalanceRecord, LedgerOperation, MonthlyDraft
from app.services.balance import create_monthly_snapshot
from app.template_utils import kst_datetime
from tests.factories import make_person, make_team
from tests.test_audit_presentation import add_operation
from tests.test_drafts import save, start
from tests.test_ledger_corrections import setup_ledger
from tests.test_profiles import form_values


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-31T20:04:29.123456+00:00", "2026-09-01 05:04:29"),
        ("2026-08-31T20:04:29Z", "2026-09-01 05:04:29"),
        ("기록 형식 확인", "기록 형식 확인"),
        ("", "미확인"),
    ],
)
def test_korean_time_accepts_recorded_iso_strings(value, expected):
    assert kst_datetime(value) == expected


def test_draft_list_shows_korean_time(auth_client, db):
    save(auth_client, start(auth_client))
    draft = db.scalar(select(MonthlyDraft))
    draft.updated_at = datetime(2026, 8, 31, 20, 4, 29, 123456, tzinfo=UTC).replace(tzinfo=None)
    db.commit()
    page = auth_client.get("/drafts").text
    assert "저장: 2026-09-01 05:04:29" in page
    assert "UTC" not in page and "123456" not in page


def test_integrity_issues_and_repair_plan_name_people(auth_client, db):
    person, records = setup_ledger(db)
    records[1].usage = 999
    person.current_amount = 7
    db.commit()
    page = auth_client.get("/ledger/integrity")
    label = "합성정정(0000 8101)"
    assert f"2026-07 {label}: 총잔액" in page.text
    assert f"{label}: 현재 잔액이 마지막 유효 관측과 다릅니다." in page.text
    assert f"인원 {person.id}:" not in page.text
    values = form_values(page)
    values["reason"] = "합성 계산 재검증"
    preview = auth_client.post("/ledger/repair/preview", data=values)
    assert f"2026-07 · {label}" in preview.text
    assert f"{label} 현재 총잔액" in preview.text


def test_applied_banner_only_right_after_apply(auth_client, db):
    _person, records = setup_ledger(db)
    records[1].usage = 999
    db.commit()
    values = form_values(auth_client.get("/ledger/integrity"))
    values["reason"] = "합성 계산 재검증"
    values = form_values(auth_client.post("/ledger/repair/preview", data=values))
    applied = auth_client.post("/ledger/repair/apply", data=values, follow_redirects=False)
    assert applied.headers["location"].endswith("?done=1")
    assert "검토한 변경을 반영했습니다" in auth_client.get(applied.headers["location"]).text
    replay = auth_client.post("/ledger/repair/apply", data=values, follow_redirects=False)
    assert replay.headers["location"] == applied.headers["location"]
    operation = db.scalar(select(LedgerOperation))
    assert operation.result_url == f"/ledger/operations/{operation.request_key}"
    later = auth_client.get(operation.result_url).text
    assert "검토한 변경을 반영했습니다" not in later
    assert "변경 이력 상세" in later


def test_monthly_operation_lists_people_by_action(auth_client, db):
    kept = make_person(db, "201", "합성유지", point_no="00000201")
    deactivated = make_person(db, "202", "합성전환", point_no="00000202", status="inactive")
    changes = [
        {"point_no": "00000201", "action": "kept"},
        {"point_no": "00000202", "action": "deactivated"},
        {"point_no": "00000203", "action": "new"},
    ]
    operation = add_operation(db, "monthly", {"month": "2026-09", "changes": changes})
    create_monthly_snapshot(
        db,
        "2026-09",
        [BalanceRecord(person_id=person.id) for person in (kept, deactivated)],
        operation_id=operation.id,
    )
    page = auth_client.get("/ledger/operations/" + operation.request_key).text
    assert re.search(r"<summary>신규 1명</summary><p>0000 0203</p>", page)
    assert re.search(r"<summary>재직 유지 1명</summary><p>합성유지</p>", page)
    assert re.search(r"<summary>비재직 전환 1명</summary><p>합성전환</p>", page)
    assert "복귀" not in page


def test_dashboard_team_is_selected_from_master(auth_client, db):
    make_team(db, "합성가팀")
    make_team(db, "합성나팀", color="#123456")
    page = auth_client.get("/dashboard", params={"team_name": "합성나팀"}).text
    assert '<select name="team_name">' in page
    assert '<option value="합성가팀" >합성가팀</option>' in page
    assert '<option value="합성나팀" selected>합성나팀</option>' in page
    removed = auth_client.get("/dashboard", params={"team_name": "지운팀"}).text
    assert '<option value="지운팀" selected>지운팀</option>' in removed


def test_password_error_keeps_operation_status(auth_client):
    response = auth_client.post(
        "/settings",
        data={"current_password": "wrong", "new_password": "x" * 9, "confirm_password": "x" * 9},
    )
    assert response.status_code == 400
    assert "운영 상태" in response.text and "장부 정합성 검사" in response.text


def test_static_assets_are_versioned(auth_client):
    from app._version import __version__

    page = auth_client.get("/people").text
    assert f'/static/css/style.css?v={__version__}"' in page
    assert re.search(r'(src|href)="/static/[^"?]+"', page) is None


def test_blank_request_start_from_upload_form_with_two_file_inputs(auth_client):
    # 브라우저 폼처럼 보안 토큰은 헤더 없이 폼 필드로, 선택하지 않은 사진칸은 filename="" 파트로 보낸다.
    token = re.search(r'name="csrf_token" value="([^"]+)"', auth_client.get("/monthly").text)
    assert token is not None
    boundary = "pbboundary"
    parts = [
        f'Content-Disposition: form-data; name="csrf_token"\r\n\r\n{token.group(1)}',
        'Content-Disposition: form-data; name="month"\r\n\r\n2026-08',
    ]
    for name in ("file", "roster_file"):
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename=""\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
    body = "".join(f"--{boundary}\r\n{part}\r\n" for part in parts) + f"--{boundary}--\r\n"
    response = auth_client.raw_request(
        "POST",
        "/monthly/start",
        content=body.encode(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    plain = auth_client.post("/monthly/start", data={"month": "2026-08"})
    # 빈 행은 입력 전이라 검수 화면이 오류 상태로 열린다. 업로드 폼에서도 같은 화면이어야 한다.
    assert response.status_code == plain.status_code
    assert 'id="add-row"' in response.text and "Too many files" not in response.text
