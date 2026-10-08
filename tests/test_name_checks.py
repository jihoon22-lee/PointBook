"""이름 재판독은 제안만 남기고 인원 식별·금액을 자동 변경하지 않는다."""

import asyncio
import json
import threading
from dataclasses import asdict, replace

import httpx
import pytest
from sqlalchemy import func, select

from app.ai.base import VisionError, VisionProvider
from app.ai.gemini import GeminiProvider
from app.config import Settings
from app.models import BalanceRecord, Person
from app.services import vision
from app.services.name_checks import compare_name_reads, similar_final_consonant
from app.services.paired_images import merge_roster
from app.services.parsing import RawRequestRow
from tests.factories import make_person, make_team
from tests.monthly_helpers import review_fields
from tests.test_gemini import PNG, ROW, envelope


def row(**values):
    return RawRequestRow(
        **{
            "name": "합성하후",
            "team": "1팀",
            "grade": "소방교",
            "amount": "60,000",
            "personal_no": "0012",
            "note": "메모\n\n보존",
            **values,
        }
    )


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("합성하후", "합성하훈", True),
        ("합성가경", "합성가겸", True),
        (" 합성하후 ", "합성하훈", True),
        ("합성하후", "합성하후", False),
        ("합성하후", "합성하호", False),
        ("합성하후", "합성하부", False),
        ("합성하후", "합성하후가", False),
        ("합성경후", "합성겸훈", False),
        ("ABC", "ABD", False),
        ("", "", False),
    ],
)
def test_only_one_hangul_final_consonant_difference(left, right, expected):
    assert similar_final_consonant(left, right) is expected


def test_independent_disagreement_preserves_every_original_business_value():
    original = row(carry="100", source_line='{"name":"합성하후"}')
    changed = row(name="합성하훈", note="재판독 비고 무시", carry="999")
    result = compare_name_reads([original], [changed])[0]
    for key in asdict(original):
        if key not in {"source_line", "source_warning", "source_issue"}:
            assert getattr(result, key) == getattr(original, key)
    assert "합성하후" in result.source_warning and "합성하훈" in result.source_warning
    assert result.source_issue and not original.source_issue
    assert json.loads(result.source_line)["이름 재확인 후보"][0]["name"] == "합성하훈"


def test_row_order_is_not_used_to_assign_name_candidates():
    first, second = row(), row(name="합성나", amount="0")
    checked = [replace(second), replace(first, name="합성하훈")]
    result = compare_name_reads([first, second], checked)
    assert "합성하훈" in result[0].source_warning
    assert "합성나" not in result[0].source_warning
    assert result[1].name == "합성나" and not result[1].source_issue


@pytest.mark.parametrize("kind", ["missing", "added", "anchor_changed", "duplicate", "invalid"])
def test_unreliable_secondary_rows_never_approve_or_replace_name(kind):
    first = row()
    checked = [row()]
    if kind == "missing":
        checked = []
    if kind == "added":
        checked += [row(name="합성추가")]
    if kind == "anchor_changed":
        checked = [row(personal_no="0099")]
    if kind == "duplicate":
        checked = [row(), row()]
    if kind == "invalid":
        checked = [row(source_issue="자료형 오류")]
    result = compare_name_reads([first], checked, roster=True)[0]
    assert result.name == first.name and result.source_issue and result.source_warning


def test_repeated_payment_anchors_compare_name_groups_without_position_guessing():
    people = [row(name="합성가", personal_no=""), row(name="합성나", personal_no="")]
    agreed = compare_name_reads(people, list(reversed(people)))
    assert not any(r.source_issue for r in agreed)
    ambiguous = compare_name_reads(people, [people[0], replace(people[1], name="합성다")])
    assert not ambiguous[0].source_issue
    assert "중복" in ambiguous[1].source_warning
    assert ambiguous[1].name == "합성나"
    duplicates = compare_name_reads([row(), row()], [row(), row()])
    assert all(r.source_issue for r in duplicates)


def test_valid_point_anchor_does_not_require_team_for_shared_account():
    first = row(point_no="0000 0012", team="", grade="", account_type="shared", personal_no="")
    assert not compare_name_reads([first], [replace(first, point_no="00000012")])[0].source_issue


def test_near_name_suggestions_do_not_copy_number_or_replace_name():
    first = row(personal_no="", source_warning="앞선 이름 재확인 경고")
    result = merge_roster([first], [row(name="합성하훈", amount="")])
    merged = result.rows[0]
    assert merged.name == first.name and merged.personal_no == merged.point_no == ""
    assert "받침이 다른 이름 후보: 합성하훈 (0012)" in merged.source_warning
    assert "앞선 이름 재확인 경고" in merged.source_warning
    assert "합성하훈" in result.import_notice


@pytest.mark.parametrize("changes", [{"team": "2팀"}, {"grade": "소방장"}, {"name": "합성하호"}])
def test_near_names_require_same_team_grade_and_vowel(changes):
    candidate = replace(row(name="합성하훈"), **changes)
    result = merge_roster([row(personal_no="")], [candidate])
    assert "받침이 다른 이름 후보" not in result.rows[0].source_warning


def test_gemini_recheck_is_independent_of_initial_names_and_uses_original_image():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope(json.dumps([ROW])))

    provider = GeminiProvider("synthetic", transport=httpx.MockTransport(respond))
    assert provider.supports_name_recheck
    provider.extract_names(PNG, "image.png", roster=True)
    prompt = requests[0]["contents"][0]["parts"][0]["text"]
    assert "독립 검사" in prompt and "종성" in prompt
    assert ROW["name"] not in prompt
    assert requests[0]["contents"][0]["parts"][1]["inline_data"]["data"]


class RecheckProvider(VisionProvider):
    supports_name_recheck = True

    def extract_table(self, image_bytes, filename):
        return [row()]

    def extract_names(self, image_bytes, filename, *, roster=False):
        return [row(name="합성하훈")]


def test_recheck_disagreement_is_saved_visible_and_resumable_without_ledger_changes(
    auth_client, db, monkeypatch
):
    team = make_team(db)
    person = make_person(db, "0012", "합성하훈", grade="소방교", team=team, point_no="00000012")
    monkeypatch.setattr(vision, "get_provider", RecheckProvider)
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-10"}, files={"file": ("image.png", PNG)}
    )
    assert response.status_code == 400
    values = review_fields(response)
    assert values["name_0"] == "합성하후" and values["point_no_0"] == ""
    assert "name-review-warning" in response.text and "재확인 ‘합성하훈’" in response.text
    assert "비재직 전환 후보 (1)" not in response.text
    reopened = auth_client.get("/drafts/" + values["draft_id"])
    values = review_fields(reopened)
    assert values["source_warning_0"] and values["name_0"] == "합성하후"
    values["name_0"] = "합성하훈"
    revised = auth_client.post("/monthly/review", data=values)
    values = review_fields(revised)
    assert values["point_no_0"] == person.point_no
    assert values["source_warning_0"] and not values["source_issue_0"]
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0
    assert db.scalar(select(func.count(Person.id))) == 1
    assert person.status == "active"


def test_secondary_failure_retains_first_read_and_requires_manual_review(monkeypatch):
    class Failing(RecheckProvider):
        def extract_names(self, image_bytes, filename, *, roster=False):
            raise VisionError("합성 재확인 장애", code="synthetic")

    monkeypatch.setattr(vision, "get_provider", Failing)
    result = asyncio.run(vision.extract_image(PNG, "image.png"))
    assert result[0].name == "합성하후" and result[0].amount == "60,000"
    assert result[0].source_issue and "완료하지 못했습니다" in result[0].source_warning


def test_secondary_timeout_keeps_first_read_and_running_worker_slot(monkeypatch):
    class Waiting(RecheckProvider):
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def extract_names(self, image_bytes, filename, *, roster=False):
            self.started.set()
            self.release.wait(5)
            return [row()]

    provider = Waiting()
    vision.shutdown_vision()
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    monkeypatch.setattr(
        vision,
        "get_settings",
        lambda: Settings.model_construct(ai_max_concurrency=1, ai_timeout_seconds=0.05),
    )

    async def run():
        try:
            result = await vision.extract_image(PNG, "image.png")
            assert provider.started.is_set()
            assert result[0].name == "합성하후" and result[0].source_warning
            with pytest.raises(VisionError, match="진행 중"):
                await vision.extract_image(PNG, "image.png")
        finally:
            provider.release.set()
            await asyncio.sleep(0.03)
            vision.shutdown_vision()

    asyncio.run(run())


def test_pair_acquires_both_original_tables_before_a_name_recheck_can_timeout(monkeypatch):
    class Pair(RecheckProvider):
        def __init__(self):
            self.calls = []
            self.release = threading.Event()

        def extract_table(self, data, filename):
            self.calls.append("payment")
            return [row(personal_no="")]

        def extract_roster(self, data, filename):
            self.calls.append("roster")
            return [row(amount="")]

        def extract_names(self, data, filename, *, roster=False):
            self.calls.append("names")
            self.release.wait(5)
            return [row()]

    provider = Pair()
    vision.shutdown_vision()
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    monkeypatch.setattr(
        vision,
        "get_settings",
        lambda: Settings.model_construct(ai_max_concurrency=1, ai_timeout_seconds=0.05),
    )

    async def run():
        try:
            payments, roster_rows = await vision.extract_image_pair(
                PNG, "payment.png", PNG, "roster.png"
            )
            assert provider.calls == ["payment", "roster", "names"]
            assert payments[0].amount == "60,000" and roster_rows[0].personal_no == "0012"
            assert payments[0].source_warning and roster_rows[0].source_warning
        finally:
            provider.release.set()
            await asyncio.sleep(0.03)
            vision.shutdown_vision()

    asyncio.run(run())


def test_used_budget_skips_secondary_without_losing_initial_rows(monkeypatch):
    class Provider(RecheckProvider):
        def extract_names(self, data, filename, *, roster=False):
            pytest.fail("budget was used by the first read")

    moments = iter([10.0, 12.0])
    # asyncio도 time.monotonic을 사용하므로 이 서비스의 clock 참조만 교체한다.
    from types import SimpleNamespace

    monkeypatch.setattr(vision, "time", SimpleNamespace(monotonic=lambda: next(moments)))
    monkeypatch.setattr(vision, "get_provider", Provider)
    monkeypatch.setattr(vision, "get_settings", lambda: Settings(ai_timeout_seconds=1))
    result = asyncio.run(vision.extract_image(PNG, "image.png"))
    assert result[0].amount == "60,000" and result[0].source_warning


def test_multiple_final_consonant_candidates_stay_manual():
    result = merge_roster(
        [row(personal_no="")], [row(name="합성하훈"), row(name="합성하훔", personal_no="0034")]
    )
    assert result.rows[0].personal_no == ""
    assert (
        "합성하훈" in result.rows[0].source_warning and "합성하훔" in result.rows[0].source_warning
    )


def test_name_warning_cannot_be_dropped_without_new_review(auth_client, db):
    from app.services.review import matches_token, review_rows

    person = make_person(db, "0012", "합성하후", grade="소방교", point_no="00000012")
    values = row(point_no=person.point_no, team="", source_warning="이름 원본 확인 필요")
    checked = review_rows(db, "2026-10", [values])
    changed = review_rows(db, "2026-10", [replace(values, source_warning="")])
    assert checked.token and not matches_token(checked.token, changed.digest)


def test_unmatched_secondary_evidence_is_kept_as_document_notice(auth_client, db, monkeypatch):
    class DifferentAnchor(RecheckProvider):
        def extract_table(self, data, filename):
            return [row(), row(name="합성나", amount="0", personal_no="0034")]

        def extract_names(self, data, filename, *, roster=False):
            return [
                row(name="합성하훈", personal_no="0099"),
                row(name="합성나", amount="0", personal_no="0034"),
            ]

    monkeypatch.setattr(vision, "get_provider", DifferentAnchor)
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-10"}, files={"file": ("image.png", PNG)}
    )
    fields = review_fields(response)
    assert fields["personal_no_0"] == "0012" and fields["name_0"] == "합성하후"
    assert "0099" in fields["import_notice"] and "합성하훈" in fields["import_notice"]
    # 첫 행을 삭제해도 대응하지 못한 재판독 원문은 초안 전체에 남는다.
    fields = {key: value for key, value in fields.items() if not key.endswith("_0")}
    revised = auth_client.post("/monthly/review", data=fields)
    saved = review_fields(revised)
    reopened = auth_client.get("/drafts/" + saved["draft_id"])
    assert review_fields(reopened)["import_notice"] == saved["import_notice"]
    assert "0099" in reopened.text and "합성하훈" in reopened.text
    assert db.scalar(select(func.count(BalanceRecord.id))) == 0


def test_roster_only_name_difference_is_visible_next_to_payment_name():
    payments = [row(personal_no="")]
    roster = compare_name_reads([row(amount="")], [row(name="합성하훈", amount="")], roster=True)
    merged = merge_roster(payments, roster).rows[0]
    assert merged.personal_no == ""
    assert "재확인 ‘합성하훈’" in merged.source_warning


def test_old_and_new_document_notices_keep_all_evidence():
    from app.services.import_notes import EVIDENCE_MARKER, join_notices, split_notice

    merged = join_notices(
        '이전 안내\n미사용 명단 원문: {"old":1}', "새 안내" + EVIDENCE_MARKER + '{"new":2}'
    )
    summary, evidence = split_notice(merged)
    assert summary == "이전 안내\n새 안내"
    assert '"old":1' in evidence and '"new":2' in evidence
