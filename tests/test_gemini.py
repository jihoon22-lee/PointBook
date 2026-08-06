import pytest

from app.ai import gemini as gemini_module
from app.ai.gemini import GeminiProvider


def _provider():
    return GeminiProvider(api_key="test-key", model="gemini-2.5-flash")


def _ok_response_json(rows_json: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": rows_json}]}}]}


def test_extract_table_success(monkeypatch):
    captured = {}

    def fake_post(url, params, json, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        captured["timeout"] = timeout

        class Resp:
            status_code = 200

            def json(self):
                return _ok_response_json(
                    '[{"personal_no": "101", "name": "김소방", "team": "1팀", "grade": "소방경", "amount": 50000, "note": ""}]'
                )

            @property
            def text(self):
                return ""

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    rows = _provider().extract_table(b"fake-image", "req.png")
    assert len(rows) == 1
    assert rows[0].personal_no == "101"
    assert rows[0].name == "김소방"
    assert rows[0].amount == 50000
    assert "generateContent" in captured["url"]
    assert captured["params"] == {"key": "test-key"}
    parts = captured["json"]["contents"][0]["parts"]
    assert parts[1]["inline_data"]["mime_type"] == "image/png"


def test_extract_table_strips_code_fence(monkeypatch):
    def fake_post(url, params, json, timeout):
        class Resp:
            status_code = 200

            def json(self):
                return _ok_response_json(
                    '```json\n[{"personal_no": "102", "name": "이소방", "amount": 30000}]\n```'
                )

            @property
            def text(self):
                return ""

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    rows = _provider().extract_table(b"x", "req.jpg")
    assert rows[0].name == "이소방"
    assert rows[0].amount == 30000


def test_extract_table_api_error(monkeypatch):
    def fake_post(url, params, json, timeout):
        class Resp:
            status_code = 400
            text = "invalid api key"

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    with pytest.raises(ValueError, match="Gemini API 오류"):
        _provider().extract_table(b"x", "req.png")


def test_extract_table_http_error(monkeypatch):
    def fake_post(url, params, json, timeout):
        raise gemini_module.httpx.ConnectError("connection refused")

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    with pytest.raises(ValueError, match="호출에 실패"):
        _provider().extract_table(b"x", "req.png")


def test_extract_table_invalid_json(monkeypatch):
    def fake_post(url, params, json, timeout):
        class Resp:
            status_code = 200

            def json(self):
                return _ok_response_json("[{broken}]")

            @property
            def text(self):
                return ""

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    with pytest.raises(ValueError, match="파싱할 수 없습니다"):
        _provider().extract_table(b"x", "req.png")


def test_extract_table_empty_response(monkeypatch):
    def fake_post(url, params, json, timeout):
        class Resp:
            status_code = 200

            def json(self):
                return {"candidates": []}

            @property
            def text(self):
                return ""

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    with pytest.raises(ValueError, match="응답 형식"):
        _provider().extract_table(b"x", "req.png")


def test_extract_table_no_brackets(monkeypatch):
    def fake_post(url, params, json, timeout):
        class Resp:
            status_code = 200

            def json(self):
                return _ok_response_json("표를 인식하지 못했습니다")

            @property
            def text(self):
                return ""

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    with pytest.raises(ValueError, match="인식하지 못했습니다"):
        _provider().extract_table(b"x", "req.png")


def test_missing_api_key():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiProvider(api_key="").extract_table(b"x", "req.png")


def test_parse_rows_amount_variants():
    rows = GeminiProvider._parse_rows(
        '[{"personal_no": "1", "name": "가", "amount": "50,000원"},'
        '{"personal_no": "2", "name": "나", "amount": 30000},'
        '{"personal_no": "3", "name": "다", "amount": ""}]'
    )
    assert [r.amount for r in rows] == [50000, 30000, 0]


def test_parse_rows_skips_non_dict():
    rows = GeminiProvider._parse_rows('[{"personal_no": "1", "name": "가"}, "junk"]')
    assert len(rows) == 1


def test_mime_type_from_filename(monkeypatch):
    seen = {}

    def fake_post(url, params, json, timeout):
        seen["mime"] = json["contents"][0]["parts"][1]["inline_data"]["mime_type"]

        class Resp:
            status_code = 200

            def json(self):
                return _ok_response_json("[]")

            @property
            def text(self):
                return ""

        return Resp()

    monkeypatch.setattr(gemini_module.httpx, "post", fake_post)
    _provider().extract_table(b"x", "photo.webp")
    assert seen["mime"] == "image/webp"
