"""번호 없는 명단은 정확한 이름+개인번호로 일괄 연결한다."""

import json
from dataclasses import asdict

import pytest
from sqlalchemy import func, select

from app.models import BalanceRecord, MonthlyDraft, Person
from app.services.parsing import RawRequestRow
from app.services.review import review_rows
from tests.factories import make_person
from tests.monthly_helpers import resolve_profile_choices, review_fields
from tests.test_monthly_linking import link, upload
from tests.test_xlsx import workbook_data


def test_33_rows_auto_connect_without_manual_click_and_keep_request_values(auth_client, db):
    rows = []
    for i in range(1, 34):
        make_person(db, f"{i:04}", f"합성대원{i}", point_no=f"{i:08}")
        rows.append(
            [i, "person", "요청 팀", f"합성대원{i}", "소방교", 100, f"{i:04}", "메모\n\n보존", 25]
        )
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-08"},
        files={"file": ("request.xlsx", workbook_data(version="2", rows=rows))},
    )
    assert response.status_code == 200
    values = review_fields(response)
    for i in range(33):
        assert values[f"point_no_{i}"] == f"{i + 1:08}"
        assert values[f"link_state_{i}"].startswith("auto:")
        assert values[f"team_{i}"] == "요청 팀" and values[f"grade_{i}"] == "소방교"
        assert values[f"carry_{i}"] == "25" and values[f"amount_{i}"] == "100"
        assert values[f"note_{i}"] == "메모\n\n보존"
    assert "인원 연결 확인" not in response.text
    draft = db.get(MonthlyDraft, values["draft_id"])
    assert len(json.loads(draft.payload_json)["rows"]) == 33
    assert json.loads(draft.payload_json)["rows"][0]["point_no"] == "00000001"
    resumed = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(resumed)["point_no_32"] == "00000033"
    blocked = auth_client.post("/monthly/confirm", data=values)
    assert blocked.status_code == 400
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    resolved = resolve_profile_choices(auth_client, resumed, side="incoming")
    values = review_fields(resolved)
    assert all(person.team is None for person in db.scalars(select(Person)))
    restored = auth_client.get("/drafts/" + values["draft_id"])
    values = review_fields(restored)
    assert values["team_32"] == "요청 팀" and values["carry_32"] == "25"
    values["ack_warnings"] = "yes"
    for _ in range(2):
        assert (
            auth_client.post("/monthly/confirm", data=values, follow_redirects=False).status_code
            == 303
        )
    assert db.scalar(select(func.count(Person.id))) == 33
    assert db.scalar(select(func.sum(BalanceRecord.total))) == 4125
    db.expire_all()
    assert all(
        person.team.name == "요청 팀" and person.grade == "소방교"
        for person in db.scalars(select(Person))
    )


@pytest.mark.parametrize(
    "name,personal", [("합성", "9999"), ("다른 합성", "0011"), ("합성", ""), ("", "0011")]
)
def test_only_both_nonempty_exact_fields_match(auth_client, db, name, personal):
    make_person(db, "0011", "합성")
    response = upload(auth_client, name, personal)
    assert response.status_code == 400
    assert review_fields(response)["point_no_0"] == ""


def test_duplicate_pair_is_only_exception_and_manual_override_sticks(auth_client, db):
    first = make_person(db, "0011", "합성", point_no="00000011")
    other = make_person(db, "0011", "합성", point_no="00000012")
    result = upload(auth_client)
    assert result.status_code == 400
    assert "인원 연결 확인 1명" in result.text
    assert f"{first.id}:{first.version}" in result.text
    assert f"{other.id}:{other.version}" in result.text
    linked = link(auth_client, result, other)
    values = review_fields(linked)
    assert values["link_state_0"] == f"manual:{other.point_no}"
    assert values["point_no_0"] == other.point_no
    # 새 기본정보로 바꾸어도 명시적인 수동 선택은 자동 매칭으로 덮어쓰지 않는다.
    values["name_0"] = "수정한 이름"
    changed = auth_client.post("/monthly/review", data=values)
    assert review_fields(changed)["point_no_0"] == other.point_no
    assert first.point_no != other.point_no


def test_auto_connection_rechecks_changed_identity_and_clears_old_carry(auth_client, db):
    make_person(db, "0011", "합성", point_no="00000011")
    other = make_person(db, "0022", "다른 합성", point_no="00000022")
    values = review_fields(upload(auth_client))
    values.update(name_0=other.name, personal_no_0=other.personal_no, carry_0="999")
    original_state = values["link_state_0"]
    # 자동 저장이 먼저 일어나도 브라우저의 오래된 포인트/이월로 확정할 수 없다.
    saved = auth_client.post("/drafts/save", data=values)
    assert saved.status_code == 200
    values.update(saved.json())
    saved_payload = json.loads(db.get(MonthlyDraft, values["draft_id"]).payload_json)
    assert saved_payload["rows"][0]["point_no"] == other.point_no
    assert saved_payload["rows"][0]["carry"] == ""
    blocked = auth_client.post("/monthly/confirm", data=values)
    assert blocked.status_code in {400, 409}
    revised = auth_client.post("/monthly/review", data=values)
    fixed = review_fields(revised)
    assert fixed["point_no_0"] == other.point_no and fixed["carry_0"] == ""
    assert fixed["link_state_0"] != original_state
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_new_duplicate_after_auto_review_requires_choice(auth_client, db):
    make_person(db, "0011", "합성", point_no="00000011")
    values = review_fields(upload(auth_client))
    duplicate = make_person(db, "0011", "합성", point_no="00000012")
    values["carry_0"] = "700"
    response = auth_client.post("/monthly/confirm", data=values)
    assert response.status_code == 400
    current = review_fields(response)
    assert current["point_no_0"] == "" and current["carry_0"] == ""
    assert f"{duplicate.id}:{duplicate.version}" in response.text
    assert "인원 연결 확인 1명" in response.text


def test_direct_point_override_is_not_auto_replaced(auth_client, db):
    make_person(db, "0011", "합성", point_no="00000011")
    other = make_person(db, "0022", "다른 합성", point_no="00000022")
    values = review_fields(upload(auth_client))
    values.update(point_no_0=other.point_no, carry_0="900")
    response = auth_client.post("/monthly/review", data=values)
    revised = review_fields(response)
    assert (
        revised["point_no_0"] == other.point_no
        and revised["link_state_0"] == f"manual:{other.point_no}"
    )
    assert revised["carry_0"] == ""


def test_review_does_not_mutate_input_or_clear_source_errors(auth_client, db):
    make_person(db, "0011", "합성", point_no="00000011")
    original = RawRequestRow(name="합성", personal_no="0011", amount="100", carry="20")
    before = asdict(original)
    result = review_rows(db, "2026-08", [original])
    assert not result.errors and result.raw_rows[0].point_no == "00000011"
    assert asdict(original) == before
    original.source_issue = "개인번호 숫자 셀: 원본 확인"
    invalid = review_rows(db, "2026-08", [original])
    assert invalid.errors and invalid.raw_rows[0].point_no == ""


def test_duplicate_request_rows_cannot_charge_same_account_twice(auth_client, db):
    make_person(db, "0011", "합성", point_no="00000011")
    pasted = "팀\t이름\t계급\t충전액\t개인번호\n1팀\t합성\t소방사\t100\t0011\n1팀\t합성\t소방사\t100\t0011"
    response = auth_client.post("/monthly/upload", data={"month": "2026-08", "pasted": pasted})
    assert response.status_code == 400 and "중복된 포인트번호" in response.text
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_repeated_direct_changes_of_manual_binding_clear_carry(auth_client, db):
    first = make_person(db, "0011", "합성", point_no="00000011")
    second = make_person(db, "0022", "다른 합성", point_no="00000022")
    values = review_fields(upload(auth_client))
    for person in (second, first):
        values.update(point_no_0=person.point_no, carry_0="500")
        response = auth_client.post("/monthly/review", data=values)
        values = review_fields(response)
        assert values["point_no_0"] == person.point_no
        assert values["link_state_0"] == f"manual:{person.point_no}"
        assert values["carry_0"] == ""


def test_unmatched_identity_can_be_corrected_and_automatically_reconnected(auth_client, db):
    person = make_person(db, "0011", "합성", point_no="00000011")
    values = review_fields(upload(auth_client))
    values.update(name_0="잘못 입력", carry_0="500")
    response = auth_client.post("/monthly/review", data=values)
    values = review_fields(response)
    assert values["point_no_0"] == "" and values["link_state_0"] == ""
    values["name_0"] = person.name
    corrected = auth_client.post("/monthly/review", data=values)
    assert corrected.status_code == 200
    assert review_fields(corrected)["point_no_0"] == person.point_no
