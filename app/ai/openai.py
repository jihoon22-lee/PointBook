"""OpenAI Chat Completions: Gemini 실패 시 같은 프롬프트로 읽는 대체 제공자."""

import base64
import copy
import json
import re
from typing import Any

import httpx

from app.ai.base import VisionError, VisionProvider
from app.ai.gemini import NAME_RECHECK_PROMPT, PROMPT, ROSTER_PROMPT
from app.ai.http import post_json
from app.ai.images import normalize_image
from app.ai.rows import load_json, parse_rows
from app.config import get_settings
from app.services.parsing import ROW_FIELDS, RawRequestRow

URL = "https://api.openai.com/v1/chat/completions"
# strict 스키마는 최상위 객체만 허용하므로 행 배열을 rows로 감싼다.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(ROW_FIELDS),
                "properties": {key: {"type": "string"} for key in ROW_FIELDS},
            },
        }
    },
}


class OpenAIProvider(VisionProvider):
    supports_name_recheck = True
    unsupported_mimes = frozenset({"image/heic"})

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-6.1-sol",
        *,
        timeout: float = 240,
        max_response_bytes: int = 2_000_000,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    def limited(self, seconds: float) -> VisionProvider:
        # 기다리는 쪽이 포기한 뒤에도 worker가 슬롯을 쥐고 응답을 기다리지 않게 한다.
        clone = copy.copy(self)
        clone._timeout = min(self._timeout, seconds)
        return clone

    def extract_table(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        return self._extract(image_bytes, filename, roster=False)

    def extract_roster(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        return self._extract(image_bytes, filename, roster=True)

    def extract_names(
        self, image_bytes: bytes, filename: str, *, roster: bool = False
    ) -> list[RawRequestRow]:
        return self._extract(image_bytes, filename, roster=roster, names_only=True)

    def _extract(
        self, image_bytes: bytes, filename: str, *, roster: bool, names_only: bool = False
    ) -> list[RawRequestRow]:
        if not self._api_key.strip():
            raise VisionError("OPENAI_API_KEY가 설정되지 않았습니다.", code="missing_key")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", self._model):
            raise VisionError("OPENAI_MODEL 설정이 올바르지 않습니다.", code="invalid_model")
        image_bytes, mime = normalize_image(
            image_bytes,
            filename,
            max_bytes=get_settings().max_upload_mb * 1024 * 1024,
            label="개인번호 명단 사진" if roster else "요청서 사진",
        )
        if mime in self.unsupported_mimes:
            raise VisionError("OpenAI는 HEIC 사진을 읽을 수 없습니다.", code="image_unsupported")
        prompt = (NAME_RECHECK_PROMPT if names_only else "") + (ROSTER_PROMPT if roster else PROMPT)
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,"
                                + base64.b64encode(image_bytes).decode(),
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "request_rows", "strict": True, "schema": SCHEMA},
            },
        }
        document = post_json(
            "OpenAI",
            URL,
            {"Authorization": f"Bearer {self._api_key}"},
            payload,
            timeout=self._timeout,
            max_response_bytes=self._max_response_bytes,
            transport=self._transport,
        )
        return parse_rows(self._rows_text(load_json(document)), roster=roster or names_only)

    @staticmethod
    def _rows_text(data: Any) -> str:
        try:
            choices = data["choices"]
            if len(choices) != 1:
                raise ValueError
            choice = choices[0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("refusal"):
                raise ValueError
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise VisionError(
                "OpenAI 응답 형식이 올바르지 않습니다.", code="response_schema"
            ) from None
        body = load_json(content)
        if not isinstance(body, dict) or set(body) != {"rows"}:
            raise VisionError("OpenAI 응답 형식이 올바르지 않습니다.", code="response_schema")
        return json.dumps(body["rows"], ensure_ascii=False)
