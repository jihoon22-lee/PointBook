"""외부 번호 없는 요청의 수동 연결과 발급 대기, 실제 HTTP 확정 경계."""

from sqlalchemy import func, select

from app.models import BalanceRecord, MonthlyDraft, MonthlySnapshot, Person
from tests.factories import make_person, make_team
from tests.monthly_helpers import review_fields


def upload(client, name="합성", personal="0011"):
    return client.post(
        "/monthly/upload",
        data={
            "month": "2026-08",
            "pasted": f"팀\t이름\t계급\t충전액\t개인번호\n1팀\t{name}\t소방사\t100\t{personal}",
        },
    )


def link(client, response, person, **overrides):
    values = review_fields(response)
    values.update(link_row=values["row_id_0"], link_person_0=f"{person.id}:{person.version}")
    values.update(overrides)
    return client.post("/monthly/link", data=values)


def test_no_number_waits_in_draft_then_external_number_registers_once(auth_client, db):
    response = upload(auth_client)
    assert response.status_code == 400
    values = review_fields(response)
    assert values["point_no_0"] == "" and not values["review_token"]
    assert db.get(MonthlyDraft, values["draft_id"]).status == "active"
    assert auth_client.post("/monthly/confirm", data=values).status_code == 400
    assert db.scalar(select(func.count(Person.id))) == 0
    values.update(point_no_0="00771122", carry_0="30")
    reviewed = auth_client.post("/monthly/review", data=values)
    assert reviewed.status_code == 200
    ready = review_fields(reviewed)
    ready["ack_warnings"] = "yes"
    for _ in range(2):
        assert (
            auth_client.post("/monthly/confirm", data=ready, follow_redirects=False).status_code
            == 303
        )
    assert db.scalar(select(func.count(Person.id))) == 1
    assert db.scalar(select(Person.point_no)) == "00771122"
    assert db.scalar(select(BalanceRecord.total)) == 130


def test_duplicate_names_require_choice_and_link_preserves_master_and_request(auth_client, db):
    team = make_team(db, "현재 팀")
    first = make_person(db, "0011", "합성", point_no="00110001", team=team)
    second = make_person(db, "0011", "합성", point_no="00110002", team=team, status="inactive")
    response = upload(auth_client)
    assert response.status_code == 400
    assert f"{first.id}:{first.version}" in response.text
    assert f"{second.id}:{second.version}" in response.text
    linked = link(auth_client, response, second, carry_0="999", note_0="\n첫째\n\n둘째\n")
    assert linked.status_code == 200
    values = review_fields(linked)
    assert values["point_no_0"] == second.point_no
    assert values["team_0"] == "현재 팀" and values["grade_0"] == second.grade
    assert values["amount_0"] == "100" and values["carry_0"] == ""
    assert values["note_0"] == "\n첫째\n\n둘째\n"
    assert "1팀" in values["source_line_0"]
    resumed = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(resumed)["point_no_0"] == second.point_no
    values.update(carry_0="30", ack_warnings="yes")
    values[f"deactivated_carry_{first.point_no}"] = "40"
    assert (
        auth_client.post("/monthly/confirm", data=values, follow_redirects=False).status_code == 303
    )
    db.expire_all()
    assert second.status == "active" and first.status == "inactive"
    record = db.scalar(select(BalanceRecord).where(BalanceRecord.person_id == second.id))
    assert record.total == 130 and record.note == values["note_0"]


def test_stale_candidate_cannot_overwrite_changed_master(auth_client, db):
    person = make_person(db, "0011", "합성")
    response = upload(auth_client)
    old_version = person.version
    person.name = "수정된 합성"
    person.version += 1
    db.commit()
    linked = link(auth_client, response, person, link_person_0=f"{person.id}:{old_version}")
    assert linked.status_code == 400 and "인원 정보가 변경" in linked.text
    assert review_fields(linked)["point_no_0"] == ""
    assert db.scalar(select(Person.name)) == "수정된 합성"
    assert db.scalar(select(func.count(MonthlySnapshot.id))) == 0


def test_shared_link_without_personal_number_and_duplicate_connection_rejected(auth_client, db):
    person = make_person(db, "", "공용 합성", point_no="00008888", account_type="shared")
    response = upload(auth_client, "공용 합성", "")
    linked = link(auth_client, response, person)
    assert linked.status_code == 200
    values = review_fields(linked)
    assert values["account_type_0"] == "shared" and values["personal_no_0"] == ""
    for key, value in list(values.items()):
        if key.endswith("_0"):
            values[key[:-2] + "_1"] = value
    values["row_id_1"] = "another-row"
    duplicate = auth_client.post("/monthly/review", data=values)
    assert duplicate.status_code == 400 and "중복된 포인트번호" in duplicate.text


def test_relink_clears_carry_and_changed_form_cannot_reuse_review(auth_client, db):
    first = make_person(db, "0011", "합성", point_no="00000011")
    other = make_person(db, "0022", "다른 합성", point_no="00000022")
    linked = link(auth_client, upload(auth_client), first)
    relinked = link(auth_client, linked, other, carry_0="900")
    values = review_fields(relinked)
    assert values["point_no_0"] == other.point_no and values["carry_0"] == ""
    values.update(carry_0="0", name_0="임의 변경", ack_warnings="yes")
    values[f"deactivated_carry_{first.point_no}"] = "0"
    assert auth_client.post("/monthly/confirm", data=values).status_code == 409
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_link_invalid_selection_keeps_input(auth_client):
    response = upload(auth_client)
    values = review_fields(response)
    values.update(link_row=values["row_id_0"], link_person_0="bad")
    invalid = auth_client.post("/monthly/link", data=values)
    assert invalid.status_code == 400
    assert review_fields(invalid)["amount_0"] == "100"
    values["link_row"] = "missing"
    assert auth_client.post("/monthly/link", data=values).status_code == 400


def test_link_and_autosave_do_not_clear_other_rows_source_errors(auth_client, db):
    from tests.test_xlsx import workbook_data

    person = make_person(db, "0011", "합성")
    data = workbook_data(
        version="2",
        rows=[
            [1, "person", "", "합성", "", 100, "0011", "", 0],
            [2, "person", "", "다른 합성", "", 50, "0022", "=1+1", 0],
        ],
        modify=lambda wb: setattr(wb["요청서"]["H6"], "data_type", "f"),
    )
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-08"}, files={"file": ("test.xlsx", data)}
    )
    values = review_fields(response)
    assert "H6" in values["source_issue_1"]
    saved = auth_client.post("/drafts/save", data=values)
    assert saved.status_code == 200
    values.update(saved.json())
    values.update(link_row=values["row_id_0"], link_person_0=f"{person.id}:{person.version}")
    linked = auth_client.post("/monthly/link", data=values)
    assert linked.status_code == 400
    returned = review_fields(linked)
    assert "H6" in returned["source_issue_1"] and not returned["review_token"]
    assert returned["point_no_0"] == person.point_no
    returned.update(point_no_1="00000022", note_1="확인한 비고")
    reviewed = auth_client.post("/monthly/review", data=returned)
    assert reviewed.status_code == 200
    assert review_fields(reviewed)["source_issue_1"] == ""


def test_numberless_paste_headers_map_values_and_keep_malformed_rows():
    import pytest

    from app.services.parsing import parse_pasted_raw

    parsed = parse_pasted_raw(
        "순번\t계정 구분\t팀\t이름\t계급\t충전액\t개인번호\t비고\t이월 잔액\n1\t일반\t1팀\t합성\t소방교\t0\t0011\t메모\t20\n2\t일반"
    )
    assert parsed[0].point_no == "" and parsed[0].personal_no == "0011"
    assert parsed[0].account_type == "person" and parsed[0].amount == "0"
    assert parsed[0].note == "메모" and parsed[0].carry == "20"
    assert parsed[1].source_issue and parsed[1].source_line == "2\t일반"
    with pytest.raises(ValueError, match="열 제목"):
        parse_pasted_raw("이름\t개인번호\t금액\t충전액\n합성\t0011\t0\t100")
