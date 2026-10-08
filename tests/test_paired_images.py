"""합성 지급표와 명단의 결합·초안·HTTP 경계를 검증한다."""

import json
import re
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import func, select

from app.ai.base import VisionProvider
from app.ai.gemini import GeminiProvider
from app.ai.rows import parse_rows
from app.models import BalanceRecord, MonthlyDraft, Person
from app.services import vision
from app.services.paired_images import merge_roster
from app.services.parsing import RawRequestRow
from app.services.review import canonical_row, matches_token, review_rows
from tests.factories import make_person
from tests.monthly_helpers import review_fields
from tests.test_gemini import PNG, ROW, envelope


def payment(**values):
    return RawRequestRow(
        **{
            "name": "합성가",
            "grade": "소방교",
            "team": "1팀",
            "amount": "60,000",
            "note": "비고\n\n보존",
            **values,
        }
    )


def roster(**values):
    return payment(personal_no="0018", amount="", **values)


def test_unique_match_keeps_payment_order_values_and_source():
    payments = [payment(), payment(name="합성나", amount="0")]
    paired = merge_roster(payments, [roster(name="합성나"), roster(grade="교")])
    result = paired.rows
    assert [row.name for row in result] == ["합성가", "합성나"]
    assert [row.amount for row in result] == ["60,000", "0"]
    assert all(row.personal_no == "0018" and row.point_no == "" for row in result)
    assert result[0].note == "비고\n\n보존"
    assert payments[0].personal_no == ""
    assert json.loads(result[0].source_line)["지급표"]["personal_no"] == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"team": "2팀"},
        {"grade": "소방장"},
        {"personal_no": "18"},
        {"personal_no": ""},
        {"source_issue": "자료형 오류"},
        {"account_type": "shared"},
    ],
)
def test_ambiguous_or_invalid_roster_never_supplies_number(changes):
    paired = merge_roster([payment()], [replace(roster(), **changes)])
    result = paired.rows
    assert result[0].personal_no == ""
    assert result[0].source_issue and result[0].source_warning
    assert result[0].team == "1팀" and result[0].grade == "소방교"


@pytest.mark.parametrize("duplicate_payment", [False, True])
def test_duplicate_keys_on_either_side_require_review(duplicate_payment):
    payments = [payment()] * (2 if duplicate_payment else 1)
    people = [roster()] * (1 if duplicate_payment else 2)
    paired = merge_roster(payments, people)
    result = paired.rows
    assert len(result) == len(payments)
    assert all(not row.personal_no and row.source_issue for row in result)


def test_existing_conflicting_number_and_unmatched_rows_are_preserved():
    paired = merge_roster(
        [payment(personal_no="0099"), payment(name="지급전용")],
        [roster(), roster(name="명단전용")],
    )
    result = paired.rows
    assert len(result) == 2 and result[0].personal_no == "0099"
    assert "번호가 다릅니다" in result[0].source_issue
    assert "명단전용" in paired.import_notice
    assert result[1].personal_no == "" and result[1].source_issue


def test_gemini_roster_contract_and_numberless_payment():
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=envelope(
                json.dumps(
                    [
                        {**ROW, "point_no": "", "personal_no": "0018", "amount": ""},
                    ]
                )
            ),
        )

    provider = GeminiProvider("synthetic", transport=httpx.MockTransport(respond))
    row = provider.extract_roster(PNG, "roster.png")[0]
    assert row.personal_no == "0018" and not row.source_issue
    assert "계급.이름" in captured[0]["contents"][0]["parts"][0]["text"]
    normal = parse_rows(json.dumps([{**ROW, "point_no": ""}]))[0]
    assert not normal.source_issue and not normal.point_no
    bad = parse_rows(json.dumps([{**ROW, "point_no": "", "amount": "5O000"}]))[0]
    assert bad.source_issue and bad.amount == "5O000"


class PairedProvider(VisionProvider):
    def extract_table(self, image_bytes, filename):
        return [payment()]

    def extract_roster(self, image_bytes, filename):
        return [roster(), roster(name="명단전용")]


def test_missing_personal_number_stays_blank_in_saved_and_reopened_draft(
    auth_client, db, monkeypatch
):
    class MissingNumberProvider(PairedProvider):
        def extract_roster(self, image_bytes, filename):
            return [roster(name="다른합성")]

    monkeypatch.setattr(vision, "get_provider", MissingNumberProvider)
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-10"},
        files={
            "file": ("payment.png", PNG),
            "roster_file": ("roster.png", PNG),
        },
    )
    assert response.status_code == 400
    values = review_fields(response)
    assert values["personal_no_0"] == values["point_no_0"] == ""
    assert "찾지 못했습니다" in response.text
    reopened = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(reopened)["personal_no_0"] == ""
    assert review_fields(reopened)["amount_0"] == "60,000"
    assert db.scalar(select(func.count(Person.id))) == 0
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_browser_multipart_upload_auto_links_and_persists_warning_without_ledger_write(
    auth_client,
    db,
    monkeypatch,
):
    person = make_person(db, "0018", "합성가", grade="소방교", point_no="00000018")
    monkeypatch.setattr(vision, "get_provider", PairedProvider)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', auth_client.get("/monthly").text)[1]
    # 실제 브라우저처럼 헤더가 아닌 multipart 필드의 CSRF 토큰으로 요청한다.
    response = auth_client.raw_request(
        "POST",
        "/monthly/upload",
        data={
            "month": "2026-10",
            "csrf_token": csrf,
        },
        files={
            "file": ("payment.png", PNG, "image/png"),
            "roster_file": ("roster.png", PNG, "image/png"),
        },
    )
    assert response.status_code == 200
    values = review_fields(response)
    assert values["point_no_0"] == person.point_no
    assert values["personal_no_0"] == "0018"
    assert values["amount_0"] == "60,000" and values["carry_0"] == ""
    assert values["month"] == "2026-10"
    assert "명단전용" in values["import_notice"]
    assert "name_1" not in values
    draft = db.get(MonthlyDraft, values["draft_id"])
    assert json.loads(draft.payload_json)["import_notice"]
    resumed = auth_client.get("/drafts/" + draft.id)
    assert review_fields(resumed)["import_notice"] == values["import_notice"]
    reviewed = auth_client.post("/monthly/review", data=review_fields(resumed))
    assert review_fields(reviewed)["import_notice"] == values["import_notice"]
    assert db.scalar(select(func.count(Person.id))) == 1
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


@pytest.mark.parametrize(
    "files,pasted",
    [
        ({"roster_file": ("roster.png", PNG)}, ""),
        ({"file": ("payment.xlsx", b"fake"), "roster_file": ("roster.png", PNG)}, ""),
        ({"file": ("payment.png", PNG), "roster_file": ("roster.png", b"fake")}, ""),
        ({"file": ("payment.png", PNG), "roster_file": ("roster.png", PNG)}, "원문"),
    ],
)
def test_invalid_combinations_fail_before_external_request(auth_client, monkeypatch, files, pasted):
    monkeypatch.setattr(vision, "get_provider", lambda: pytest.fail("AI must not run"))
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-10", "pasted": pasted}, files=files
    )
    assert response.status_code == 400
    assert not pasted or pasted in response.text


def test_document_evidence_survives_first_row_deletion_and_requires_new_review(auth_client, db):
    first = make_person(db, "0011", "합성가", point_no="00000011")
    second = make_person(db, "0022", "합성나", point_no="00000022")
    notice = "미사용 명단 합성다 · 0033"
    response = auth_client.post(
        "/monthly/review",
        data={
            "month": "2026-10",
            "import_notice": notice,
            "point_no_0": first.point_no,
            "personal_no_0": "0011",
            "name_0": "합성가",
            "amount_0": "100",
            "account_type_0": "person",
            "point_no_1": second.point_no,
            "personal_no_1": "0022",
            "name_1": "합성나",
            "amount_1": "200",
            "account_type_1": "person",
        },
    )
    values = review_fields(response)
    values = {key: value for key, value in values.items() if not key.endswith("_0")}
    response = auth_client.post("/monthly/review", data=values)
    values = review_fields(response)
    assert values["name_0"] == "합성나" and values["import_notice"] == notice
    reopened = auth_client.get("/drafts/" + values["draft_id"])
    assert review_fields(reopened)["import_notice"] == notice
    rows = [payment(point_no=second.point_no, personal_no="0022", name="합성나", team="", grade="")]
    original = review_rows(db, "2026-10", rows, import_notice=notice)
    changed = review_rows(db, "2026-10", rows)
    assert not matches_token(original.token, changed.digest)
    assert "source_warning" not in canonical_row(RawRequestRow())


@pytest.mark.parametrize(
    "filenames", [["file", "file"], ["file", "unexpected"], ["file", "roster_file", "extra"]]
)
def test_extra_or_duplicate_files_are_not_silently_ignored(auth_client, monkeypatch, filenames):
    monkeypatch.setattr(vision, "get_provider", lambda: pytest.fail("AI must not run"))
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-10"},
        files=[(key, ("image.png", PNG)) for key in filenames],
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "amount,expected", [("60,000", 60000), ("0", 0), ("", None), ("5O000", None)]
)
def test_unlinked_rows_still_show_input_total_without_treating_invalid_money_as_zero(
    auth_client, db, amount, expected
):
    result = review_rows(db, "2026-10", [payment(amount=amount)])
    assert result.errors
    assert result.input_amount == expected
