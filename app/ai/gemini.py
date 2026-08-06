"""Gemini Vision 프로바이더 — 요청서 사진에서 테이블을 추출한다.

REST API(generateContent)를 httpx로 호출한다. 모델은 GEMINI_MODEL로 설정
(기본 gemini-2.5-flash). API 키는 GEMINI_API_KEY (.env).
"""

import base64
import json
import re
from typing import Any

import httpx

from app.ai.base import VisionProvider
from app.services.sync import RequestRow

PROMPT = """이미지 속 요청서 표를 읽어라. 표 컬럼은 순번, 팀, 이름, 계급, 금액, 개인번호, 비고 순서이다.
헤더 행은 제외하고 데이터 행만 추출해라. 각 행을 다음 키를 가진 JSON 객체로 변환해라:
{"personal_no": "개인번호(문자열)", "name": "이름", "team": "팀", "grade": "계급", "amount": 금액(숫자, 콤마 제거), "note": "비고"}
결과는 JSON 배열 하나만 출력하고, 설명이나 코드펜스(markdown)는 붙이지 마라."""

MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


def _parse_amount(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").replace(",", "").replace("원", "").strip()
    match = re.search(r"\d+", text)
    return int(match.group()) if match else 0


class GeminiProvider(VisionProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._api_key = api_key
        self._model = model

    def extract_table(self, image_bytes: bytes, filename: str) -> list[RequestRow]:
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인해 주세요.")
        ext = f".{filename.lower().rsplit('.', 1)[-1]}" if "." in filename else ""
        mime = MIME_BY_EXT.get(ext, "image/jpeg")
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
            "generationConfig": {"temperature": 0.1},
        }
        try:
            response = httpx.post(url, params={"key": self._api_key}, json=payload, timeout=90)
        except httpx.HTTPError as exc:
            raise ValueError(f"Gemini API 호출에 실패했습니다: {exc}") from exc
        if response.status_code != 200:
            raise ValueError(f"Gemini API 오류 ({response.status_code}): {response.text[:200]}")
        text = self._response_text(response.json())
        return self._parse_rows(text)

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(str(p.get("text", "")) for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Gemini 응답 형식이 올바르지 않습니다.") from exc

    @staticmethod
    def _parse_rows(text: str) -> list[RequestRow]:
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
        cleaned = re.sub(r"\s*```", "", cleaned).strip()
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            raise ValueError(
                "Gemini가 요청서 테이블을 인식하지 못했습니다. 사진을 다시 촬영해 주세요."
            )
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("Gemini 응답을 파싱할 수 없습니다.") from exc
        rows: list[RequestRow] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            rows.append(
                RequestRow(
                    personal_no=str(item.get("personal_no", "")).strip(),
                    name=str(item.get("name", "")).strip(),
                    team=str(item.get("team", "")).strip(),
                    grade=str(item.get("grade", "")).strip(),
                    amount=_parse_amount(item.get("amount")),
                    note=str(item.get("note", "")).strip(),
                )
            )
        return rows
