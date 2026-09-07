"""R14/R15/R17: 프로필 승인·현재 관측·고정 과거 정보의 실제 HTTP 회귀."""

import json
from html.parser import HTMLParser

import pytest
from sqlalchemy import func, select

from app.models import (
    BalanceAdjustment,
    BalanceRecord,
    LedgerOperation,
    LedgerState,
    MonthlySnapshot,
)
from app.services.balance import create_monthly_snapshot
from app.services.dates import current_month
from app.services.history import profile_for_person
from app.services.validation import MAX_MONEY, MAX_TOTAL
from tests.factories import make_person, make_team


class ProfileForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.select_name = None
        self.textarea_name = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            self.values[attrs["name"]] = attrs.get("value", "")
        elif tag == "select":
            self.select_name = attrs.get("name")
        elif tag == "option" and "selected" in attrs:
            self.values[self.select_name] = attrs.get("value", "")
        elif tag == "textarea":
            self.textarea_name = attrs.get("name")
            self.values[self.textarea_name] = ""

    def handle_data(self, text):
        if self.textarea_name:
            self.values[self.textarea_name] += text

    def handle_endtag(self, tag):
        if tag == "textarea":
            self.textarea_name = None


def form_values(response):
    form = ProfileForm()
    form.feed(response.text)
    return form.values


def _submit_profile(client, path, *, data=None, **kwargs):
    """기존 업무 테스트도 실제 브라우저처럼 최신 폼→검토→승인으로 요청한다."""
    if path != "/people/new" and not path.endswith("/edit"):
        return client.post(path, data=data, **kwargs)
    opened = form_values(client.get(path))
    metadata = {
        key: opened[key]
        for key in ("request_key", "base_version", "person_version", "initial_month")
        if key in opened
    }
    submitted = {**metadata, "reason": "합성 프로필 변경 검토", **(data or {}), "intent": "preview"}
    preview = client.post(path, data=submitted, **kwargs)
    token = form_values(preview).get("plan_token")
    if preview.status_code != 200 or not token:
        return preview
    return client.post(
        path,
        data={**submitted, "intent": "apply", "plan_token": token, "confirm_identity": "yes"},
        **kwargs,
    )


def _new_form(client, **overrides):
    return {
        **form_values(client.get("/people/new")),
        "point_no": "00000001",
        "personal_no": "합성1",
        "name": "합성 인원",
        "intent": "preview",
        **overrides,
    }


def _edit_form(client, person, **overrides):
    return {
        **form_values(client.get(f"/people/{person.id}/edit")),
        "reason": "합성 기본정보 정정",
        "intent": "preview",
        **overrides,
    }


def _apply(client, path, data, preview, **overrides):
    return client.post(
        path,
        data={
            **data,
            "intent": "apply",
            "plan_token": form_values(preview)["plan_token"],
            **overrides,
        },
        follow_redirects=False,
    )


def test_new_account_preview_has_no_writes_then_initial_observation(auth_client, db):
    data = _new_form(auth_client, carry_balance="700", amount="300")
    preview = auth_client.post("/people/new", data=data)
    assert preview.status_code == 200
    assert "1,000원" in preview.text
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 0
    result = _apply(auth_client, "/people/new", data, preview)
    assert result.status_code == 303
    adjustment = db.scalar(select(BalanceAdjustment))
    assert adjustment.total == 1000 and adjustment.month == current_month()
    assert adjustment.note == "계정 등록 초기 잔액"
    assert json.loads(adjustment.profile_data)["point_no"] == "00000001"
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0
    assert "별도 잔액 관측" in auth_client.get(result.headers["location"]).text


@pytest.mark.parametrize(
    "carry,amount,total", [("0", "0", 0), (str(MAX_MONEY), str(MAX_MONEY), MAX_TOTAL)]
)
def test_known_zero_and_max_initial_balance_are_observations(auth_client, db, carry, amount, total):
    data = _new_form(auth_client, carry_balance=carry, amount=amount)
    preview = auth_client.post("/people/new", data=data)
    result = _apply(auth_client, "/people/new", data, preview)
    assert result.status_code == 303
    adjustment = db.scalar(select(BalanceAdjustment))
    assert adjustment.total == total
    from app.models import Person

    person = db.scalar(select(Person))
    assert person.current_carry_balance == total and person.current_amount == 0
    assert "월간 충전 실적" in auth_client.get(result.headers["location"]).text


def test_new_registration_replay_and_changed_payload(auth_client, db):
    data = _new_form(auth_client)
    preview = auth_client.post("/people/new", data=data)
    first = _apply(auth_client, "/people/new", data, preview)
    repeated = _apply(auth_client, "/people/new", data, preview)
    assert repeated.status_code == 303 and repeated.headers["location"] == first.headers["location"]
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 1
    assert db.scalar(select(func.count(LedgerOperation.id))) == 1
    conflicting = _apply(auth_client, "/people/new", data, preview, name="다른 입력")
    assert conflicting.status_code == 409


def test_profile_changes_require_review_and_preserve_historical_rows(auth_client, db):
    old_team = make_team(db, "기존 팀")
    new_team = make_team(db, "새 팀")
    person = make_person(db, point_no="00000001", name="기존 이름", team=old_team)
    frozen = json.dumps(profile_for_person(person), ensure_ascii=False)
    snapshot = create_monthly_snapshot(
        db,
        "2026-01",
        [
            BalanceRecord(
                person_id=person.id,
                carry_balance=100,
                amount=50,
                usage=-20,
                total=150,
                profile_data=frozen,
                provenance="observed",
                note="당시 비고",
            )
        ],
    )
    record = snapshot.records[0]
    before = (
        record.profile_data,
        record.note,
        record.carry_balance,
        record.amount,
        record.usage,
        record.total,
        record.version,
    )
    person.current_carry_balance = 100
    person.current_amount = 50
    db.commit()
    data = _edit_form(
        auth_client,
        person,
        name="현재 이름",
        team_id=str(new_team.id),
        grade="새 계급",
        status="inactive",
    )
    preview = auth_client.post(f"/people/{person.id}/edit", data=data)
    assert preview.status_code == 200
    db.refresh(person)
    assert person.name == "기존 이름"
    assert _apply(auth_client, f"/people/{person.id}/edit", data, preview).status_code == 303
    db.refresh(person)
    db.refresh(record)
    assert person.name == "현재 이름" and person.team_id == new_team.id and person.version == 2
    assert (person.current_carry_balance, person.current_amount) == (100, 50)
    assert (
        record.profile_data,
        record.note,
        record.carry_balance,
        record.amount,
        record.usage,
        record.total,
        record.version,
    ) == before
    page = auth_client.get(f"/people/{person.id}")
    assert "기존 이름" in page.text and "기존 팀" in page.text and "당시 비고" in page.text
    assert "확정 당시 관측" in page.text and "-20" in page.text
    assert f"/ledger/correct/{person.id}?month=2026-01" in page.text
    operation = db.scalar(select(LedgerOperation))
    assert operation.kind == "profile"
    assert json.loads(operation.detail_json)["changes"][0]["before"]["name"] == "기존 이름"


def test_identity_change_needs_explicit_impact_approval(auth_client, db):
    person = make_person(db, point_no="00000001")
    data = _edit_form(
        auth_client, person, point_no="00000002", account_type="shared", personal_no=""
    )
    preview = auth_client.post(f"/people/{person.id}/edit", data=data)
    assert "변경의 영향을 확인" in preview.text
    assert _apply(auth_client, f"/people/{person.id}/edit", data, preview).status_code == 400
    db.refresh(person)
    assert person.point_no == "00000001" and person.account_type == "person"
    assert (
        _apply(
            auth_client, f"/people/{person.id}/edit", data, preview, confirm_identity="yes"
        ).status_code
        == 303
    )
    db.refresh(person)
    assert (
        person.point_no == "00000002"
        and person.account_type == "shared"
        and person.personal_no is None
    )


def test_stale_profile_tab_and_rebase_preserve_newer_state(auth_client, db):
    person = make_person(db, point_no="00000001")
    first = _edit_form(auth_client, person, name="첫 저장")
    second = _edit_form(auth_client, person, name="오래된 탭 입력")
    first_preview = auth_client.post(f"/people/{person.id}/edit", data=first)
    second_preview = auth_client.post(f"/people/{person.id}/edit", data=second)
    assert _apply(auth_client, f"/people/{person.id}/edit", first, first_preview).status_code == 303
    stale = _apply(auth_client, f"/people/{person.id}/edit", second, second_preview)
    assert stale.status_code == 409 and 'value="오래된 탭 입력"' in stale.text
    db.refresh(person)
    assert person.name == "첫 저장"
    rebased = auth_client.post(f"/people/{person.id}/edit", data={**second, "intent": "rebase"})
    assert rebased.status_code == 200 and "첫 저장" in rebased.text
    apply_data = {
        **second,
        **{
            key: value
            for key, value in form_values(rebased).items()
            if key in {"base_version", "person_version"}
        },
    }
    assert _apply(auth_client, f"/people/{person.id}/edit", apply_data, rebased).status_code == 303


def test_tampering_after_preview_is_rejected_without_profile_or_audit_changes(auth_client, db):
    person = make_person(db, point_no="00000001")
    data = _edit_form(auth_client, person, name="검토한 이름")
    preview = auth_client.post(f"/people/{person.id}/edit", data=data)
    response = _apply(
        auth_client, f"/people/{person.id}/edit", data, preview, name="나중에 바꾼 이름"
    )
    assert response.status_code == 409
    db.refresh(person)
    assert person.name != "나중에 바꾼 이름"
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_profile_edit_cannot_submit_money_even_without_history(auth_client, db):
    person = make_person(db, point_no="00000001")
    person.current_carry_balance, person.current_amount = 700, 300
    db.commit()
    data = _edit_form(auth_client, person, carry_balance="9,000", amount="1,000")
    response = auth_client.post(f"/people/{person.id}/edit", data=data)
    assert response.status_code == 400 and "금액을 변경할 수 없습니다" in response.text
    assert 'name="carry_balance"' not in response.text and 'name="amount"' not in response.text
    assert "9,000" in response.text and "1,000" in response.text
    db.refresh(person)
    assert person.current_carry_balance + person.current_amount == 1000
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 0


def test_legacy_unknown_history_is_not_filled_from_live_profile(auth_client, db):
    person = make_person(db, point_no="00000001", name="현재 이름")
    snapshot = MonthlySnapshot(month="2024-01")
    db.add(snapshot)
    db.flush()
    db.add_all(
        [
            BalanceRecord(
                person_id=person.id,
                snapshot_id=snapshot.id,
                carry_balance=0,
                amount=100,
                usage=0,
                total=100,
                profile_data=json.dumps(
                    {"name": "이관 참고 이름", "account_type": "person", "status": "inactive"}
                ),
                provenance="master_at_migration",
            )
        ],
    )
    db.commit()
    page = auth_client.get(f"/people/{person.id}")
    assert "이관 참고 이름" in page.text
    assert "당시 정보 미확인 · 이관 시점 인원 정보 참고" in page.text
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 0


def test_profile_backup_failure_preserves_everything(auth_client, db, monkeypatch):
    from app.services import profiles

    person = make_person(db, point_no="00000001")
    base = db.scalar(select(LedgerState.version))
    data = _edit_form(auth_client, person, name="백업 실패 입력")
    preview = auth_client.post(f"/people/{person.id}/edit", data=data)
    monkeypatch.setattr(profiles, "backup_database", lambda: None)
    response = _apply(auth_client, f"/people/{person.id}/edit", data, preview)
    assert response.status_code == 400
    db.refresh(person)
    assert person.name != "백업 실패 입력"
    assert db.scalar(select(LedgerState.version)) == base
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


@pytest.mark.parametrize("missing", ["request_key", "base_version", "reason", "person_version"])
def test_profile_submission_requires_review_metadata(auth_client, db, missing):
    person = make_person(db, point_no="00000001")
    data = _edit_form(auth_client, person, name="저장하면 안 되는 입력")
    del data[missing]
    response = auth_client.post(f"/people/{person.id}/edit", data=data)
    assert response.status_code in {400, 409}
    db.refresh(person)
    assert person.name != "저장하면 안 되는 입력"
    assert db.scalar(select(func.count(LedgerOperation.id))) == 0


def test_new_initial_observation_does_not_change_legacy_unobserved_account(auth_client, db):
    legacy = make_person(db, point_no="00000009", name="기존 미관측")
    legacy.current_carry_balance, legacy.current_amount = 700, 300
    db.commit()
    data = _new_form(auth_client)
    preview = auth_client.post("/people/new", data=data)
    assert _apply(auth_client, "/people/new", data, preview).status_code == 303
    assert (
        db.scalar(
            select(func.count(BalanceAdjustment.id)).where(BalanceAdjustment.person_id == legacy.id)
        )
        == 0
    )
    page = auth_client.get(f"/people/{legacy.id}")
    assert "관측 기준 월 미확인" in page.text and "1,000원" in page.text


def test_new_initial_observation_cannot_be_backdated_from_hidden_month(auth_client, db):
    data = _new_form(auth_client, initial_month="2000-01")
    response = auth_client.post("/people/new", data=data)
    assert response.status_code == 409
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 0


def test_profile_failure_after_person_insert_rolls_back_atomically(auth_client, db, monkeypatch):
    from app.models import Person
    from app.services import profiles

    data = _new_form(auth_client)
    preview = auth_client.post("/people/new", data=data)
    before_version = db.scalar(select(LedgerState.version))

    def fail_operation(*args, **kwargs):
        raise ValueError("합성 감사 저장 실패")

    monkeypatch.setattr(profiles, "add_operation", fail_operation)
    response = _apply(auth_client, "/people/new", data, preview)
    assert response.status_code == 400
    assert db.scalar(select(func.count(Person.id))) == 0
    assert db.scalar(select(func.count(BalanceAdjustment.id))) == 0
    assert db.scalar(select(LedgerState.version)) == before_version


def test_profile_backup_permission_failure_preserves_inputs_without_path_leak(
    auth_client, db, monkeypatch
):
    from app.services import profiles

    person = make_person(db, point_no="00000001")
    data = _edit_form(auth_client, person, name="보존할 이름")
    preview = auth_client.post(f"/people/{person.id}/edit", data=data)

    def fail_backup():
        raise PermissionError("SENSITIVE-PATH")

    monkeypatch.setattr(profiles, "backup_database", fail_backup)
    response = _apply(auth_client, f"/people/{person.id}/edit", data, preview)
    assert response.status_code == 400
    assert 'value="보존할 이름"' in response.text
    assert "SENSITIVE-PATH" not in response.text
    db.refresh(person)
    assert person.name != "보존할 이름"
