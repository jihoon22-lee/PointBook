"""포인트번호 정규화와 표시 형식."""

import re

POINT_NO_RE = re.compile(r"^\d{8}$")
LEGACY_POINT_NO_RE = re.compile(r"^L\d{7}$")


def normalize_point_no(value: str) -> str:
    """공백·하이픈을 제거하고 숫자 8자리 포인트번호를 반환한다."""
    normalized = re.sub(r"[\s-]", "", value.strip())
    if not POINT_NO_RE.fullmatch(normalized):
        raise ValueError("포인트번호는 숫자 8자리여야 합니다.")
    return normalized


def format_point_no(value: str | None) -> str:
    """저장된 포인트번호를 화면용 `0000 0000` 형식으로 표시한다."""
    if value is None:
        return "-"
    if not POINT_NO_RE.fullmatch(value):
        return "미전환"
    return f"{value[:4]} {value[4:]}"


def is_legacy_point_no(value: str | None) -> bool:
    """Alembic이 기존 계정에 부여한 일회성 전환용 값인지 확인한다."""
    return bool(value and LEGACY_POINT_NO_RE.fullmatch(value))
