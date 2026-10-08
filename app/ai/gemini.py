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
제목·산정기관·근무일자·인원 합계·금액 합계는 인원 행이 아니다. 팀 병합 셀은 해당 인원에 적용해라.
기울어진 사진에서는 화면의 수평 높이로 팀 경계를 나누지 마라. 각 인원 행의 선을 따라 왼쪽 팀 병합 셀까지 확인해라. 경계 바로 위·아래 행을 특히 대조하고, 팀 경계를 읽을 수 없으면 추정하지 말고 team을 빈 문자열로 남겨라.
개인별 금액은 지급액/출동간식비 소계(원) 열을 읽고, 출동·상황·당직 건수를 금액으로 바꾸지 마라.
각 객체 키는 point_no, personal_no, name, team, grade, amount, note, account_type 이다.
모든 값은 문자열이다. 읽은 그대로 보존하고 숫자 일부 추출, 부호 삭제, 빈 금액을 0으로 바꾸지 마라.
포인트번호와 개인번호의 선행 0을 유지해라. 포인트번호는 외부 시스템 발급 번호라 요청서에 없을 수 있다. 없는 번호를 이름·개인번호·순번에서 생성하거나 추정하지 마라. 모르는 셀은 빈 문자열로 남겨라.
account_type은 일반 인원 person, 명시된 공용 계정 shared, 알 수 없으면 빈 문자열이다.
설명이나 코드펜스를 붙이지 마라."""

ROSTER_PROMPT = """이미지 속 팀별 개인번호 명단의 모든 인원 행을 순서대로 읽어라.
각 객체 키는 point_no, personal_no, name, team, grade, amount, note, account_type인 JSON 배열이다.
모든 값은 문자열. 팀 병합 셀은 해당 인원에 적용한다.
사진이 기울어져 있으면 행의 선을 따라 팀 경계를 확인하며, 모르는 팀은 추정하지 않는다.
'경.김합성'처럼 계급.이름을 분리하고
경=소방경, 위=소방위, 장=소방장, 교=소방교, 사=소방사로 계급을 적는다.
인원 옆 4자리 개인번호를 선행 0을 보존해 personal_no에 적는다. 전화/내선/인원수/제목은 인원이 아니다.
명단의 순서나 다른 인원을 근거로 번호를 추정하지 마라. 불완전 인원 행도 버리지 마라.
point_no와 amount는 빈 문자열, account_type은 person, 모르는 셀은 빈 문자열로 둔다.
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
        return self._extract(image_bytes, filename, roster=False)

    def extract_roster(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        return self._extract(image_bytes, filename, roster=True)

    def _extract(self, image_bytes: bytes, filename: str, *, roster: bool) -> list[RawRequestRow]:
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
                        {"text": ROSTER_PROMPT if roster else PROMPT},
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
            return parse_rows(self._response_text(load_json(document)), roster=roster)
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
