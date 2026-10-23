"""검수 입력의 정규화·명시 유형·Excel 실제 범위·CSV 실패 경계 회귀."""

import json
import re

import pytest
from sqlalchemy import select

from app.ai.rows import parse_rows
from app.models import BalanceRecord, MonthlyDraft
from app.services.parsing import MAX_RECORD_CHARS, parse_pasted_raw
from app.services.vision import ImageRead
from app.services.xlsx import read_request
from tests.factories import make_person
from tests.monthly_helpers import resolve_profile_choices, review_fields
from tests.test_monthly_profile_choices import choose, confirm, existing, request_row
from tests.test_xlsx import mutate_zip, workbook_data


@pytest.mark.parametrize("point", ["0000-0011", "0000 0011", "0000\u00a00011", "0000\t0011"])
def test_formatted_point_profile_choice_draft_and_confirmation(auth_client, db, point):
    person = existing(db)
    response = request_row(auth_client, point_no_0=point)
    assert response.status_code == 200
    selected = resolve_profile_choices(auth_client, response)
    assert selected.status_code == 200
    values = review_fields(selected)
    assert values["point_no_0"] == point
    assert values["review_token"] and values["carry_0"] == "35"
    restored = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(restored)["point_no_0"] == point
    assert confirm(auth_client, restored).status_code == 303
    db.expire_all()
    assert person.point_no == "00000011"
    assert person.team.name == "요청팀" and person.grade == "소방교"
    assert db.scalar(select(BalanceRecord)).total == 135


def test_formatted_point_does_not_allow_stale_profile_selection(auth_client, db):
    person = existing(db)
    response = request_row(auth_client, point_no_0="0000-0011")
    person.name = "변경된 합성대원"
    person.version += 1
    db.commit()
    assert choose(auth_client, response, "team").status_code == 409
    assert db.scalar(select(BalanceRecord)) is None


@pytest.mark.parametrize("source", ["paste", "image", "xlsx"])
@pytest.mark.parametrize("kind", [None, "", "person", "shared", "기타"])
def test_upload_only_complements_omitted_account_type(auth_client, db, monkeypatch, source, kind):
    make_person(db, "0011", "합성공용", grade="", point_no="00000011", account_type="shared")
    data = {"month": "2026-08"}
    if source == "paste":
        cells = ["", "합성공용", "", "100", "0011", "00000011", ""]
        if kind is not None:
            cells.append(kind)
        response = auth_client.post("/monthly/upload", data={**data, "pasted": "\t".join(cells)})
    elif source == "image":
        raw = {
            "point_no": "00000011",
            "personal_no": "0011",
            "name": "합성공용",
            "team": "",
            "grade": "",
            "amount": "100",
            "note": "",
        }
        if kind is not None:
            raw["account_type"] = kind

        async def read_images(documents):
            return [ImageRead(parse_rows(json.dumps([raw], ensure_ascii=False)))]

        monkeypatch.setattr("app.routers.monthly.read_images", read_images)
        response = auth_client.post(
            "/monthly/upload", data=data, files={"file": ("synthetic.png", b"synthetic")}
        )
    else:
        document = workbook_data(
            rows=[[1, kind, "", "합성공용", "", 100, "0011", "00000011", "", 0]]
        )
        response = auth_client.post(
            "/monthly/upload", data=data, files={"file": ("synthetic.xlsx", document)}
        )
    values = review_fields(response)
    expected = kind or ("person" if source == "xlsx" else "shared")
    assert values["account_type_0"] == expected
    if expected == "person":
        assert not values["review_token"]
        assert ":account_type:current" in response.text
        selected = resolve_profile_choices(auth_client, response, side="current")
        assert review_fields(selected)["account_type_0"] == "shared"
    elif kind == "기타":
        assert not values["review_token"]
    else:
        assert values["review_token"]
    assert (
        "account_type_input"
        not in json.loads(db.get(MonthlyDraft, values["draft_id"]).payload_json)["rows"][0]
    )


def test_explicit_type_choice_survives_draft_restore(auth_client, db):
    make_person(db, "0011", "합성공용", grade="", point_no="00000011", account_type="shared")
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-08", "pasted": "\t합성공용\t\t100\t0011\t00000011\t\tperson"},
    )
    values = review_fields(response)
    restored = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(restored)["account_type_0"] == "person"
    assert not review_fields(restored)["review_token"]
    selected = resolve_profile_choices(auth_client, restored, side="current")
    saved = review_fields(selected)
    reopened = auth_client.get("/drafts/" + saved["draft_id"])
    assert review_fields(reopened)["account_type_0"] == "shared"
    assert review_fields(reopened)["review_token"]
    assert "person" in review_fields(reopened)["source_line_0"]


def _dimension(data, reference):
    def change(name, content):
        if name == "xl/worksheets/sheet1.xml":
            replacement = b"" if reference is None else f'<dimension ref="{reference}"/>'.encode()
            content = re.sub(rb'<dimension ref="[^"]+"\s*/>', replacement, content)
        return content

    return mutate_zip(data, change=change)


@pytest.mark.parametrize("version", ["1", "2"])
@pytest.mark.parametrize("reference", ["A1:J5", "A1:A1", None])
def test_excel_reads_actual_rows_despite_stale_or_missing_dimension(version, reference):
    rows = [
        [1, "person", "", "합성첫째", "", 100, "0011", "00000011", "첫째", 0],
        [2, "person", "", "합성둘째", "", 200, "0012", "00000012", "둘째", 0],
    ]
    if version == "2":
        rows = [row[:7] + row[8:] for row in rows]
    document = _dimension(workbook_data(version=version, rows=rows), reference)
    parsed = read_request(document, "synthetic.xlsx")
    assert [row.name for row in parsed.rows] == ["합성첫째", "합성둘째"]


def test_stale_excel_dimension_does_not_deactivate_a_present_person(auth_client, db):
    make_person(db, "0011", "합성첫째", grade="", point_no="00000011")
    make_person(db, "0012", "합성둘째", grade="", point_no="00000012")
    document = _dimension(
        workbook_data(
            rows=[
                [1, "person", "", "합성첫째", "", 100, "0011", "00000011", "", 0],
                [2, "person", "", "합성둘째", "", 200, "0012", "00000012", "", 0],
            ]
        ),
        "A1:J5",
    )
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-08"}, files={"file": ("synthetic.xlsx", document)}
    )
    values = review_fields(response)
    assert values["name_1"] == "합성둘째"
    assert not any(key.startswith("deactivated_carry_") for key in values)
    assert confirm(auth_client, response).status_code == 303
    assert len(list(db.scalars(select(BalanceRecord)))) == 2


@pytest.mark.parametrize("version,column", [("1", "K"), ("2", "J")])
def test_excel_stale_dimension_cannot_hide_extra_columns(version, column):
    rows = [[1, "person", "", "합성", "", 100, "0011", "00000011", "", 0]]
    if version == "2":
        rows = [rows[0][:7] + rows[0][8:]]
    document = _dimension(
        workbook_data(
            version=version,
            rows=rows,
            modify=lambda wb: setattr(wb["요청서"][column + "5"], "value", "숨은 값"),
        ),
        "A1:A1",
    )
    with pytest.raises(ValueError, match="열"):
        read_request(document, "synthetic.xlsx")


def test_excel_reordered_cells_cannot_hide_extra_column_during_dimension_recalculation():
    document = workbook_data(
        version="2",
        rows=[[1, "person", "", "합성", "", 100, "0011", "비고", 0]],
        modify=lambda wb: setattr(wb["요청서"]["J5"], "value", "숨은 값"),
    )

    def reorder(name, content):
        if name == "xl/worksheets/sheet1.xml":
            cell = re.search(rb'<c r="J5".*?</c>', content).group(0)
            content = content.replace(cell, b"").replace(b'<row r="5">', b'<row r="5">' + cell)
        return content

    with pytest.raises(ValueError, match="열"):
        read_request(mutate_zip(document, change=reorder), "synthetic.xlsx")


@pytest.mark.parametrize("length", [MAX_RECORD_CHARS + 1, 131_073])
def test_long_unquoted_record_is_preserved_without_losing_the_next_row(auth_client, length):
    raw = "x" * length
    text = raw + "\n\t합성다음\t\t100\t0012\t00000012\n"
    rows = parse_pasted_raw(text)
    assert rows[0].source_line == raw
    assert rows[0].source_issue
    assert rows[1].name == "합성다음"
    response = auth_client.post("/monthly/upload", data={"month": "2026-08", "pasted": text})
    assert response.status_code in {400, 413}
    values = review_fields(response)
    assert values["source_line_0"] == raw and values["name_1"] == "합성다음"
    assert not values["review_token"]


def test_unquoted_record_character_limit_counts_content_not_line_ending():
    prefix = "\t합성한도\t\t100\t0011\t00000011\t"
    raw = prefix + "x" * (MAX_RECORD_CHARS - len(prefix))
    rows = parse_pasted_raw(raw + "\r\n")
    assert rows[0].source_line == raw and not rows[0].source_issue
    assert rows[0].name == "합성한도"
