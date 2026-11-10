import base64
import json

import httpx
import pytest

from app.ai.base import VisionError
from app.ai.gemini import GeminiProvider
from app.ai.rows import parse_rows
from app.services.parsing import MAX_REQUEST_ROWS
from app.services.validation import MAX_MONEY

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)
ROW = {
    "point_no": "0000 0001",
    "personal_no": "101",
    "name": "합성 인원",
    "team": "1팀",
    "grade": "소방경",
    "amount": "50,000",
    "note": "",
    "account_type": "person",
}


def envelope(text):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]}


@pytest.fixture(autouse=True)
def retry_waits(monkeypatch):
    """재시도 대기를 실제로 기다리지 않고 기록한다."""
    waits = []
    monkeypatch.setattr("app.ai.http.time.sleep", waits.append)
    return waits


def provider(handler, **kwargs):
    return GeminiProvider(
        "synthetic-key-never-real", transport=httpx.MockTransport(handler), **kwargs
    )


def test_extract_table_preserves_raw_values_and_uses_header_auth():
    captured = []

    def respond(request):
        captured.append(request)
        return httpx.Response(200, json=envelope(json.dumps([ROW])))

    rows = provider(respond).extract_table(PNG, "req.png")
    assert rows[0].point_no == "0000 0001"
    assert rows[0].amount == "50,000"
    assert rows[0].validated().amount == 50000
    assert rows[0].validated().point_no == "00000001"
    assert json.loads(rows[0].source_line) == ROW
    request = captured[0]
    assert "synthetic-key" not in str(request.url)
    assert not request.url.query
    assert request.headers["x-goog-api-key"] == "synthetic-key-never-real"
    payload = json.loads(request.content)
    assert "temperature" not in payload["generationConfig"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]["type"] == "array"
    assert payload["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503, 302])
def test_upstream_error_body_and_key_never_exposed(status, caplog):
    sensitive = "synthetic-private-body-00001111"
    instance = provider(lambda request: httpx.Response(status, text=sensitive))
    with pytest.raises(VisionError) as error:
        instance.extract_table(PNG, "req.png")
    assert str(status) in str(error.value)
    assert "문의 ID" in str(error.value)
    assert sensitive not in str(error.value) + caplog.text
    assert "synthetic-key" not in str(error.value) + caplog.text


@pytest.mark.parametrize(
    "exception", [httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError]
)
def test_network_exception_is_redacted(exception, caplog):
    def respond(request):
        raise exception("synthetic-key-never-real / private-person-number")

    with pytest.raises(VisionError, match="호출에 실패") as error:
        provider(respond).extract_table(PNG, "req.png")
    assert "code=network" in caplog.text
    assert "synthetic-key" not in str(error.value) + caplog.text
    assert "private-person" not in str(error.value) + caplog.text


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.WriteTimeout])
def test_slow_response_is_reported_as_timeout_not_network_failure(exception, caplog):
    calls = []

    def respond(request):
        calls.append(request)
        raise exception("synthetic-key-never-real / private-person-number")

    with pytest.raises(VisionError, match="시간이 초과") as error:
        provider(respond).extract_table(PNG, "req.png")
    assert len(calls) == 1  # 이미 예산을 다 쓴 긴 호출은 다시 보내지 않는다.
    assert "code=timeout" in caplog.text
    assert "synthetic-key" not in str(error.value) + caplog.text
    assert "private-person" not in str(error.value) + caplog.text


def test_response_wait_uses_whole_budget_instead_of_thirty_seconds():
    """표 전체 인식은 첫 바이트까지 30초를 넘는다. 대기 한도는 전체 예산이어야 한다."""
    captured = []

    def respond(request):
        captured.append(request.extensions["timeout"])
        return httpx.Response(200, json=envelope(json.dumps([ROW])))

    provider(respond, timeout=180).extract_table(PNG, "req.png")
    assert captured[0]["read"] == pytest.approx(180, abs=1)
    assert captured[0]["connect"] <= 10


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_upstream_status_is_retried(status, retry_waits):
    statuses = [status, status, 200]

    def respond(request):
        code = statuses.pop(0)
        if code != 200:
            return httpx.Response(code, text="synthetic overloaded")
        return httpx.Response(200, json=envelope(json.dumps([ROW])))

    rows = provider(respond).extract_table(PNG, "req.png")
    assert rows[0].name == "합성 인원"
    assert not statuses
    assert len(retry_waits) == 2


def test_persistent_transient_status_stops_after_bounded_retries(retry_waits):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, text="synthetic overloaded")

    with pytest.raises(VisionError, match="503"):
        provider(respond).extract_table(PNG, "req.png")
    assert len(calls) == 3
    assert len(retry_waits) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
def test_permanent_upstream_status_is_not_retried(status, retry_waits):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="synthetic rejected")

    with pytest.raises(VisionError, match=str(status)):
        provider(respond).extract_table(PNG, "req.png")
    assert len(calls) == 1
    assert not retry_waits


def test_retry_never_waits_past_the_budget(retry_waits):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, text="synthetic overloaded")

    with pytest.raises(VisionError, match="503"):
        provider(respond, timeout=1).extract_table(PNG, "req.png")
    assert len(calls) == 1
    assert not retry_waits


@pytest.mark.parametrize(
    "text",
    [
        "prefix [] suffix",
        "```json\n[]\n```",
        "[{broken}]",
        "[NaN]",
        '{"rows": []}',
        "[]",
        "",
        "null",
    ],
)
def test_complete_json_required_and_empty_response_rejected(text):
    with pytest.raises(VisionError):
        provider(lambda request: httpx.Response(200, json=envelope(text))).extract_table(
            PNG, "req.png"
        )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"candidates": []},
        {"candidates": [None]},
        {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "[]"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": 5}]}}]},
        {"candidates": [{"content": {"parts": []}}]},
    ],
)
def test_response_envelope_must_be_complete(data):
    with pytest.raises(VisionError, match="응답 형식"):
        provider(lambda request: httpx.Response(200, json=data)).extract_table(PNG, "req.png")


def test_non_json_http_body_is_redacted():
    with pytest.raises(VisionError, match="JSON") as error:
        provider(
            lambda request: httpx.Response(200, content=b"private-upstream-body")
        ).extract_table(PNG, "req.png")
    assert "private-upstream" not in str(error.value)


@pytest.mark.parametrize(
    "amount", ["", "-1,000", "12.5", "5O000", "abc", None, True, False, 12.5, MAX_MONEY + 1]
)
def test_invalid_amount_stays_in_review_as_raw_error(amount):
    rows = parse_rows(json.dumps([{**ROW, "amount": amount}]))
    assert len(rows) == 1
    assert json.loads(rows[0].source_line)["amount"] == amount
    with pytest.raises(ValueError):
        rows[0].validated()


def test_incomplete_and_non_object_rows_are_never_silently_skipped():
    items = [ROW, {"name": "합성 불완전"}, None, "합성 원문", [1, 2]]
    rows = parse_rows(json.dumps(items))
    assert len(rows) == len(items)
    assert len({row.row_id for row in rows}) == len(items)
    assert not rows[0].source_issue
    assert all(row.source_issue for row in rows[1:])
    assert [json.loads(row.source_line) for row in rows] == items


def test_identifier_number_is_not_padded_or_treated_as_string():
    row = parse_rows(json.dumps([{**ROW, "point_no": 12345678}]))[0]
    assert row.point_no == "12345678"
    assert "자료형" in row.source_issue
    with pytest.raises(ValueError):
        row.validated()


def test_shared_null_personal_number_and_integer_zero():
    row = parse_rows(
        json.dumps([{**ROW, "account_type": "shared", "personal_no": None, "amount": 0}])
    )[0]
    assert row.amount == "0"
    assert row.validated().personal_no == ""


def test_unknown_fields_require_review():
    assert parse_rows(json.dumps([{**ROW, "confidence": 0.99}]))[0].source_issue


def test_response_row_limit():
    with pytest.raises(VisionError, match="2000행"):
        parse_rows(json.dumps([ROW] * (MAX_REQUEST_ROWS + 1)))


def test_response_stream_stops_at_byte_limit_and_closes():
    class Stream(httpx.SyncByteStream):
        consumed = 0
        closed = False

        def __iter__(self):
            for chunk in [b"a" * 20, b"b" * 100, b"never-read"]:
                self.consumed += 1
                yield chunk

        def close(self):
            self.closed = True

    stream = Stream()
    with pytest.raises(VisionError, match="응답 크기"):
        provider(
            lambda request: httpx.Response(200, stream=stream), max_response_bytes=50
        ).extract_table(PNG, "req.png")
    assert stream.consumed == 2
    assert stream.closed


@pytest.mark.parametrize(
    "content,headers", [(b"\xff\xfe", {}), (b"fake", {"content-encoding": "gzip"})]
)
def test_invalid_encoding_is_rejected(content, headers):
    with pytest.raises(VisionError):
        provider(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(content), headers=headers)
        ).extract_table(PNG, "req.png")


def test_missing_key_and_invalid_model_do_not_make_requests():
    for kwargs in [{"api_key": ""}, {"api_key": "test", "model": "secret?key=test"}]:
        with pytest.raises(VisionError):
            GeminiProvider(**kwargs).extract_table(PNG, "req.png")


def test_stream_total_deadline_is_enforced(monkeypatch):
    moments = iter([0.0, 0.0])  # 기한 계산과 요청 시작. 이후 응답 수신 중에 기한을 넘긴다.
    monkeypatch.setattr("app.ai.http.time.monotonic", lambda: next(moments, 2.0))
    with pytest.raises(VisionError, match="시간이 초과"):
        provider(
            lambda request: httpx.Response(200, json=envelope(json.dumps([ROW]))), timeout=1
        ).extract_table(PNG, "req.png")


def test_duplicate_json_keys_do_not_silently_replace_cells():
    with pytest.raises(VisionError, match="JSON"):
        parse_rows('[{"point_no":"00000001","amount":"-10","amount":"10"}]')
