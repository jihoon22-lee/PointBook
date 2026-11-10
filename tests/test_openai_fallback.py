import base64
import json

import httpx
import pytest

from app.ai.base import VisionError, VisionProvider
from app.ai.factory import get_provider
from app.ai.fallback import FallbackProvider
from app.ai.gemini import GeminiProvider
from app.ai.openai import OpenAIProvider
from app.config import Settings
from app.services import vision
from app.services.parsing import RawRequestRow

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 16
ROW = {
    "point_no": "",
    "personal_no": "0101",
    "name": "합성 인원",
    "team": "1팀",
    "grade": "소방경",
    "amount": "50,000",
    "note": "",
    "account_type": "",
}


@pytest.fixture(autouse=True)
def no_real_waits(monkeypatch):
    monkeypatch.setattr("app.ai.http.time.sleep", lambda seconds: None)


def completion(content, *, finish="stop", refusal=None):
    return {
        "choices": [{"finish_reason": finish, "message": {"content": content, "refusal": refusal}}]
    }


def openai(handler, **kwargs):
    return OpenAIProvider("synthetic-openai-key", transport=httpx.MockTransport(handler), **kwargs)


def test_openai_reads_rows_with_bearer_auth_and_strict_schema():
    captured = []

    def respond(request):
        captured.append(request)
        return httpx.Response(200, json=completion(json.dumps({"rows": [ROW]})))

    rows = openai(respond).extract_table(PNG, "req.png")
    assert (rows[0].name, rows[0].personal_no, rows[0].amount) == ("합성 인원", "0101", "50,000")
    assert rows[0].account_type == "person"
    request = captured[0]
    assert request.headers["authorization"] == "Bearer synthetic-openai-key"
    assert "synthetic-openai-key" not in str(request.url)
    payload = json.loads(request.content)
    image = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")
    assert payload["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize(
    "body",
    [
        completion(json.dumps({"rows": [ROW]}), finish="length"),
        completion(None, refusal="synthetic refusal"),
        completion(json.dumps({"rows": []})),
        completion(json.dumps([ROW])),
        completion("not json"),
        {"choices": []},
    ],
)
def test_openai_incomplete_or_refused_response_is_rejected(body):
    with pytest.raises(VisionError):
        openai(lambda request: httpx.Response(200, json=body)).extract_table(PNG, "req.png")


def test_openai_error_body_and_key_are_never_exposed(caplog):
    sensitive = "synthetic-private-body-00001111"
    with pytest.raises(VisionError, match="OpenAI API 오류 \\(401\\)") as error:
        openai(lambda request: httpx.Response(401, text=sensitive)).extract_table(PNG, "req.png")
    assert sensitive not in str(error.value) + caplog.text
    assert "synthetic-openai-key" not in str(error.value) + caplog.text


def test_openai_rejects_heic_without_calling_the_api():
    calls = []
    with pytest.raises(VisionError, match="HEIC"):
        openai(lambda request: calls.append(request)).extract_table(HEIC, "req.heic")
    assert not calls


class Scripted(VisionProvider):
    supports_name_recheck = True

    def __init__(self, error=None, *, name="합성"):
        self.error = error
        self.name = name
        self.calls = []

    def _run(self, kind):
        self.calls.append(kind)
        if self.error is not None:
            raise self.error()
        return [RawRequestRow(name=self.name, personal_no="0101", amount="1")]

    def extract_table(self, image_bytes, filename):
        return self._run("table")

    def extract_roster(self, image_bytes, filename):
        return self._run("roster")

    def extract_names(self, image_bytes, filename, *, roster=False):
        return self._run("names")


def failing(code):
    return lambda: VisionError("합성 실패", code=code)


def fallback(primary, secondary, *, timeout=240.0, budgets=None):
    def make(remaining):
        if budgets is not None:
            budgets.append(remaining)
        return secondary

    return FallbackProvider(
        primary, make, timeout=timeout, primary_label="Gemini", secondary_label="OpenAI"
    )


@pytest.mark.parametrize("code", ["upstream_status", "network", "response_schema", "empty_rows"])
def test_primary_service_failure_uses_secondary(code):
    primary, secondary = Scripted(failing(code)), Scripted(name="대체")
    budgets = []
    provider = fallback(primary, secondary, budgets=budgets)
    assert provider.extract_table(PNG, "req.png")[0].name == "대체"
    assert provider.fallback_used
    assert 0 < budgets[0] <= 240


def test_fallback_stays_on_secondary_for_the_rest_of_the_request():
    primary, secondary = Scripted(failing("upstream_status")), Scripted(name="대체")
    provider = fallback(primary, secondary)
    provider.extract_table(PNG, "req.png")
    provider.extract_roster(PNG, "roster.png")
    provider.extract_names(PNG, "req.png")
    assert primary.calls == ["table"]
    assert secondary.calls == ["table", "roster", "names"]


def test_healthy_primary_never_touches_secondary():
    primary, secondary = Scripted(name="기본"), Scripted(name="대체")
    provider = fallback(primary, secondary)
    assert provider.extract_table(PNG, "req.png")[0].name == "기본"
    assert not secondary.calls
    assert not provider.fallback_used


@pytest.mark.parametrize("code", ["timeout", "image_format", "image_limit", "busy"])
def test_timeout_and_image_errors_do_not_fall_back(code):
    primary, secondary = Scripted(failing(code)), Scripted()
    with pytest.raises(VisionError, match="합성 실패"):
        fallback(primary, secondary).extract_table(PNG, "req.png")
    assert not secondary.calls


def test_image_the_secondary_cannot_read_keeps_the_primary_error():
    primary, secondary = Scripted(failing("upstream_status")), Scripted()
    secondary.unsupported_mimes = frozenset({"image/heic"})
    with pytest.raises(VisionError, match="합성 실패"):
        fallback(primary, secondary).extract_table(HEIC, "req.heic")
    assert not secondary.calls


def test_no_fallback_when_the_budget_is_nearly_spent(monkeypatch):
    moments = iter([0.0, 235.0])
    monkeypatch.setattr("app.ai.fallback.time.monotonic", lambda: next(moments, 235.0))
    primary, secondary = Scripted(failing("upstream_status")), Scripted()
    with pytest.raises(VisionError, match="합성 실패"):
        fallback(primary, secondary).extract_table(PNG, "req.png")
    assert not secondary.calls


def test_secondary_failure_is_reported_as_its_own_error():
    primary = Scripted(failing("upstream_status"))
    secondary = Scripted(lambda: VisionError("대체 실패", code="upstream_status"))
    with pytest.raises(VisionError, match="대체 실패"):
        fallback(primary, secondary).extract_table(PNG, "req.png")


def test_format_the_primary_cannot_read_goes_to_secondary():
    primary = Scripted(failing("image_unsupported"))
    secondary = Scripted(name="대체")
    assert fallback(primary, secondary).extract_table(HEIC, "req.heic")[0].name == "대체"


def test_limited_copy_shares_fallback_state_and_caps_the_budget():
    primary, secondary = Scripted(failing("upstream_status")), Scripted(name="대체")
    budgets = []
    provider = fallback(primary, secondary, budgets=budgets)
    provider.limited(30).extract_names(PNG, "req.png")
    assert provider.fallback_used
    assert budgets[0] <= 30
    provider.limited(25).extract_names(PNG, "req.png")
    assert budgets[1] <= 25
    assert primary.calls == ["names"]


@pytest.mark.parametrize("make", [openai, None])
def test_limited_provider_never_waits_past_the_callers_budget(make):
    captured = []

    def respond(request):
        captured.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=completion(json.dumps({"rows": [ROW]})))

    if make is None:
        instance = GeminiProvider("synthetic", transport=httpx.MockTransport(respond), timeout=240)
        with pytest.raises(VisionError):  # OpenAI 형식 응답이라 Gemini는 거부한다. 기한만 본다.
            instance.limited(40).extract_table(PNG, "req.png")
    else:
        make(respond, timeout=240).limited(40).extract_table(PNG, "req.png")
    assert captured[0] <= 40


def test_openai_primary_uses_gemini_as_fallback(monkeypatch):
    base = {"ai_provider": "openai", "openai_api_key": "synthetic-openai"}
    monkeypatch.setattr("app.ai.factory.get_settings", lambda: Settings(**base))
    assert isinstance(get_provider(), OpenAIProvider)
    monkeypatch.setattr(
        "app.ai.factory.get_settings", lambda: Settings(**base, gemini_api_key="synthetic-gemini")
    )
    provider = get_provider()
    assert isinstance(provider, FallbackProvider)
    assert (provider.primary_label, provider.secondary_label) == ("OpenAI", "Gemini")
    Settings(**base).validate_runtime()
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(ai_provider="openai").validate_runtime()


def test_recheck_only_fallback_is_reported_as_recheck(monkeypatch):
    class TableOk(Scripted):
        def _run(self, kind):
            if kind == "names":
                self.calls.append(kind)
                raise VisionError("합성 실패", code="upstream_status")
            return super()._run(kind)

    provider = fallback(TableOk(), Scripted())
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    import asyncio

    reads = asyncio.run(vision.read_images([(PNG, "req.png", False)]))
    assert "이름 재확인" in reads[0].notice and "OpenAI" in reads[0].notice
    assert "사진을 인식" not in reads[0].notice


def test_factory_adds_openai_fallback_only_when_its_key_is_set(monkeypatch):
    base = {"ai_provider": "gemini", "gemini_api_key": "synthetic-gemini"}
    monkeypatch.setattr("app.ai.factory.get_settings", lambda: Settings(**base))
    assert isinstance(get_provider(), GeminiProvider)
    monkeypatch.setattr(
        "app.ai.factory.get_settings", lambda: Settings(**base, openai_api_key="synthetic-openai")
    )
    assert isinstance(get_provider(), FallbackProvider)


def test_review_notice_tells_which_service_read_the_photo(monkeypatch):
    provider = fallback(Scripted(failing("upstream_status")), Scripted(name="대체"))
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    import asyncio

    reads = asyncio.run(vision.read_images([(PNG, "req.png", False), (PNG, "roster.png", True)]))
    assert "OpenAI가 사진을 인식" in reads[0].notice
    assert "OpenAI가 사진을 인식" in reads[1].notice


def test_no_fallback_notice_when_primary_succeeds(monkeypatch):
    provider = fallback(Scripted(), Scripted())
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    import asyncio

    reads = asyncio.run(vision.read_images([(PNG, "req.png", False)]))
    assert "OpenAI" not in reads[0].notice
