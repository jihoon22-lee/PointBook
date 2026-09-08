"""Gemini generateContent: header 인증, 제한된 스트림, 서버 원문 검증."""

import base64
import re
import time
from typing import Any

import httpx

from app.ai.base import VisionError, VisionProvider
from app.ai.images import image_mime
from app.ai.rows import load_json, parse_rows
from app.services.parsing import ROW_FIELDS, RawRequestRow

PROMPT = """이미지 속 요청서 표를 읽어라. 표 컬럼은 순번, 팀, 이름, 계급, 금액, 개인번호, 포인트번호, 비고이다.
헤더를 제외한 모든 데이터 행을 순서대로 JSON 배열로 출력해라. 불완전한 행도 누락하지 마라.
각 객체 키는 point_no, personal_no, name, team, grade, amount, note, account_type 이다.
모든 값은 문자열이다. 읽은 그대로 보존하고 숫자 일부 추출, 부호 삭제, 빈 금액을 0으로 바꾸지 마라.
포인트번호와 개인번호의 선행 0을 유지해라. 포인트번호는 외부 시스템 발급 번호라 요청서에 없을 수 있다. 없는 번호를 이름·개인번호·순번에서 생성하거나 추정하지 마라. 모르는 셀은 빈 문자열로 남겨라.
account_type은 일반 인원 person, 명시된 공용 계정 shared, 알 수 없으면 빈 문자열이다.
설명이나 코드펜스를 붙이지 마라."""


class GeminiProvider(VisionProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        *,
        timeout: float = 90,
        max_response_bytes: int = 2_000_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    def extract_table(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        if not self._api_key.strip():
            raise VisionError("GEMINI_API_KEY가 설정되지 않았습니다.", code="missing_key")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", self._model):
            raise VisionError("GEMINI_MODEL 설정이 올바르지 않습니다.", code="invalid_model")
        mime = image_mime(image_bytes, filename)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(image_bytes).decode(),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {key: {"type": "string"} for key in ROW_FIELDS},
                        "required": list(ROW_FIELDS),
                    },
                },
            },
        }
        deadline = time.monotonic() + self._timeout
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    timeout=min(self._timeout, 30),
                    follow_redirects=False,
                ) as client,
                client.stream(
                    "POST",
                    url,
                    headers={"x-goog-api-key": self._api_key, "Accept-Encoding": "identity"},
                    json=payload,
                ) as response,
            ):
                if response.status_code != 200:
                    raise VisionError(
                        f"Gemini API 오류 ({response.status_code})가 발생했습니다.",
                        code="upstream_status",
                    )
                # Reject compressed transfer instead of decompressing an unbounded body.
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise VisionError(
                        "AI 응답 압축 형식을 처리할 수 없습니다.", code="response_encoding"
                    )
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > self._max_response_bytes:
                        raise VisionError(
                            "AI 응답 크기가 허용 한도를 초과했습니다.", code="response_limit"
                        )
                    if time.monotonic() > deadline:
                        raise VisionError("AI 인식 시간이 초과되었습니다.", code="timeout")
                    chunks.extend(chunk)
            try:
                document = bytes(chunks).decode("utf-8")
            except UnicodeDecodeError:
                raise VisionError(
                    "AI 응답 문자 형식이 올바르지 않습니다.", code="response_encoding"
                ) from None
            return parse_rows(self._response_text(load_json(document)))
        except httpx.HTTPError:
            raise VisionError("Gemini API 호출에 실패했습니다.", code="network") from None

    @staticmethod
    def _response_text(data: Any) -> str:
        try:
            candidates = data["candidates"]
            if len(candidates) != 1:
                raise ValueError
            candidate = candidates[0]
            if candidate.get("finishReason") not in {None, "STOP"}:
                raise ValueError
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list) or not parts:
                raise ValueError
            texts = [p["text"] for p in parts if not p.get("thought", False)]
            if not texts or any(not isinstance(t, str) for t in texts):
                raise ValueError
            return "".join(texts)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise VisionError(
                "Gemini 응답 형식이 올바르지 않습니다.", code="response_schema"
            ) from None

    _parse_rows = staticmethod(parse_rows)
