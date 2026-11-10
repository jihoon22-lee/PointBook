"""AI 제공자 공통 HTTP 호출: 전체 기한, 제한된 응답, 혼잡 응답 재시도."""

import time
from typing import Any

import httpx

from app.ai.base import VisionError

CONNECT_TIMEOUT = 10.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})
RETRY_WAITS = (3.0, 6.0)  # 첫 요청 뒤 최대 두 번, 남은 예산 안에서만 기다린다.


def post_json(
    service: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout: float,
    max_response_bytes: int,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """200 응답의 원문. 외부 오류 본문·키는 예외와 로그에 넣지 않는다."""
    deadline = time.monotonic() + timeout
    waits = list(RETRY_WAITS)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VisionError("AI 인식 시간이 초과되었습니다.", code="timeout")
        try:
            status, document = _post(
                url, headers, payload, remaining, deadline, max_response_bytes, transport
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout):
            # 예산을 다 쓴 긴 호출이다. 연결 실패와 구분하고 다시 보내지 않는다.
            raise VisionError("AI 인식 시간이 초과되었습니다.", code="timeout") from None
        except httpx.HTTPError:
            raise VisionError(f"{service} API 호출에 실패했습니다.", code="network") from None
        if status == 200:
            return document
        # 혼잡(5xx)은 생성 전에 곧바로 거절되므로 남은 예산 안에서만 다시 시도한다.
        wait = waits.pop(0) if status in RETRY_STATUSES and waits else None
        if wait is None or time.monotonic() + wait >= deadline:
            raise VisionError(
                f"{service} API 오류 ({status})가 발생했습니다.", code="upstream_status"
            )
        time.sleep(wait)


def _post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    remaining: float,
    deadline: float,
    max_response_bytes: int,
    transport: httpx.BaseTransport | None,
) -> tuple[int, str]:
    """상태 코드와 제한된 응답 원문. 200이 아닌 응답의 본문은 읽지 않는다."""
    # 생성형 응답은 생성이 끝난 뒤에야 첫 바이트를 보낸다. 표 전체 인식은 30초를
    # 넘기므로 응답 대기는 남은 전체 예산까지 허용하고 연결 단계만 짧게 제한한다.
    timeout = httpx.Timeout(remaining, connect=min(remaining, CONNECT_TIMEOUT))
    with (
        httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client,
        client.stream(
            "POST", url, headers={**headers, "Accept-Encoding": "identity"}, json=payload
        ) as response,
    ):
        if response.status_code != 200:
            return response.status_code, ""
        # Reject compressed transfer instead of decompressing an unbounded body.
        if response.headers.get("content-encoding", "identity") != "identity":
            raise VisionError("AI 응답 압축 형식을 처리할 수 없습니다.", code="response_encoding")
        chunks = bytearray()
        for chunk in response.iter_bytes():
            if len(chunks) + len(chunk) > max_response_bytes:
                raise VisionError("AI 응답 크기가 허용 한도를 초과했습니다.", code="response_limit")
            if time.monotonic() > deadline:
                raise VisionError("AI 인식 시간이 초과되었습니다.", code="timeout")
            chunks.extend(chunk)
    try:
        return 200, bytes(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise VisionError(
            "AI 응답 문자 형식이 올바르지 않습니다.", code="response_encoding"
        ) from None
