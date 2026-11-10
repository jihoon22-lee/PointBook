"""기본 제공자의 서비스 실패를 대체 제공자로 넘긴다. 사진·시간 초과 오류는 넘기지 않는다."""

import time
from collections.abc import Callable

from app.ai.base import VisionError, VisionProvider
from app.ai.images import image_mime
from app.logging import get_logger
from app.services.parsing import RawRequestRow

# 기본 제공자 쪽 원인(한도·혼잡·연결·응답 형식·설정·읽지 못하는 형식)만 넘긴다.
FALLBACK_CODES = frozenset(
    {
        "upstream_status",
        "network",
        "response_schema",
        "response_encoding",
        "response_limit",
        "invalid_json",
        "empty_rows",
        "row_limit",
        "missing_key",
        "invalid_model",
        "image_unsupported",
    }
)
MIN_FALLBACK_SECONDS = 20.0


class FallbackProvider(VisionProvider):
    """요청 하나에 쓰는 인스턴스. 한 번 넘어가면 남은 호출은 대체 제공자가 맡는다."""

    def __init__(
        self,
        primary: VisionProvider,
        make_secondary: Callable[[float], VisionProvider],
        *,
        timeout: float,
        primary_label: str,
        secondary_label: str,
        state: dict[str, bool] | None = None,
    ) -> None:
        self._primary = primary
        self._make_secondary = make_secondary
        self._timeout = timeout
        self.primary_label = primary_label
        self.secondary_label = secondary_label
        self.supports_name_recheck = primary.supports_name_recheck
        # limited()가 만든 사본과 전환 여부를 공유한다.
        self._state = state if state is not None else {"used": False}

    @property
    def fallback_used(self) -> bool:
        return self._state["used"]

    def limited(self, seconds: float) -> VisionProvider:
        return FallbackProvider(
            self._primary.limited(seconds),
            self._make_secondary,
            timeout=min(self._timeout, seconds),
            primary_label=self.primary_label,
            secondary_label=self.secondary_label,
            state=self._state,
        )

    def extract_table(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        return self._run(image_bytes, filename, lambda p: p.extract_table(image_bytes, filename))

    def extract_roster(self, image_bytes: bytes, filename: str) -> list[RawRequestRow]:
        return self._run(image_bytes, filename, lambda p: p.extract_roster(image_bytes, filename))

    def extract_names(
        self, image_bytes: bytes, filename: str, *, roster: bool = False
    ) -> list[RawRequestRow]:
        return self._run(
            image_bytes,
            filename,
            lambda p: p.extract_names(image_bytes, filename, roster=roster),
        )

    def _run(
        self,
        image_bytes: bytes,
        filename: str,
        call: Callable[[VisionProvider], list[RawRequestRow]],
    ) -> list[RawRequestRow]:
        began = time.monotonic()
        if not self.fallback_used:
            try:
                return call(self._primary)
            except VisionError as error:
                if error.code not in FALLBACK_CODES:
                    raise
                remaining = self._timeout - (time.monotonic() - began)
                if remaining < MIN_FALLBACK_SECONDS:
                    raise
                secondary = self._make_secondary(remaining)
                if image_mime(image_bytes, filename) in secondary.unsupported_mimes:
                    raise
                get_logger("pointbook.vision").warning(
                    "AI 대체 제공자 사용 %s -> %s code=%s reference=%s",
                    self.primary_label,
                    self.secondary_label,
                    error.code,
                    error.reference,
                )
                self._state["used"] = True
                return call(secondary)
        return call(self._make_secondary(self._timeout))
