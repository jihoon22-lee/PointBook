"""일반 입력의 금액 계약. 누적 장부 이관의 별도 파싱 규칙에는 적용하지 않는다."""

import re

# 입력 필드 상한과 계산된 총 잔액 범위를 구분한다. SQLite signed 64-bit 안에
# 각각 저장할 수 있다. 여러 계정 집계는 Python 정수로 합산하여 SUM overflow를 피한다.
MAX_MONEY = 999_999_999_999
MAX_TOTAL = MAX_MONEY * 2
_MONEY = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)")


def parse_money(value: object, *, label: str = "금액") -> int:
    """0 이상 정수 원화 입력을 전체 검증한다.

    Python int 또는 ASCII 숫자 문자열을 허용한다. 문자열은 양끝 공백,
    선택적인 ₩ 접두사·원 접미사, 정확한 천단위 쉼표를 허용한다.
    내부 공백, 부호, 소수, bool/float, 빈 문자열 및 일부 숫자 추출은 허용하지 않는다.
    """
    return _parse_bounded(value, label=label, maximum=MAX_MONEY, currency=True)


def parse_expected_total(value: object, *, max_rows: int = 2000) -> int:
    """기대 충전 총액은 개별 원화 문법과 전체 요청서 행수×입력 상한을 적용한다."""
    return _parse_bounded(
        value,
        label="기대 총액",
        maximum=MAX_MONEY * max_rows,
        currency=True,
    )


def parse_expected_count(value: object, *, max_rows: int = 2000) -> int:
    """기대 인원은 정수 또는 양끝 공백을 제외한 ASCII 숫자만 허용한다."""
    return _parse_bounded(value, label="기대 인원", maximum=max_rows, currency=False)


def _parse_bounded(value: object, *, label: str, maximum: int, currency: bool) -> int:
    unit = "원" if currency else "명"
    format_error = (
        f"{label}: 0 이상의 정수 원화 금액을 입력해 주세요."
        if currency
        else f"{label}: 0 이상의 정수를 숫자로만 입력해 주세요."
    )
    range_error = f"{label}: 0{unit} 이상 {maximum:,}{unit} 이하로 입력해 주세요."
    if type(value) is int:
        result = int(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if currency:
            cleaned = cleaned.removeprefix("₩").removesuffix("원")
        pattern = _MONEY if currency else re.compile(r"[0-9]+")
        if not pattern.fullmatch(cleaned):
            raise ValueError(format_error)
        digits = cleaned.replace(",", "").lstrip("0") or "0"
        if len(digits) > len(str(maximum)):
            raise ValueError(range_error)
        result = int(digits)
    else:
        raise ValueError(format_error)
    if not 0 <= result <= maximum:
        raise ValueError(range_error)
    return result
