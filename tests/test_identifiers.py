import pytest

from app.services.identifiers import format_point_no, is_legacy_point_no, normalize_point_no


@pytest.mark.parametrize("raw", ["0000 0001", "0000-0001", "00000001"])
def test_normalize_point_no_preserves_leading_zeroes(raw: str) -> None:
    assert normalize_point_no(raw) == "00000001"


@pytest.mark.parametrize("raw", ["", "1234567", "123456789", "abcd1234", "0002_6147"])
def test_normalize_point_no_rejects_non_eight_digit_values(raw: str) -> None:
    with pytest.raises(ValueError, match="포인트번호"):
        normalize_point_no(raw)


def test_format_point_no_adds_display_space() -> None:
    assert format_point_no("00000001") == "0000 0001"


def test_format_point_no_marks_legacy_value_as_unconverted() -> None:
    assert format_point_no("L0000001") == "미전환"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("L0000001", True), ("L1234567", True), ("12345678", False), (None, False)],
)
def test_is_legacy_point_no(value: str | None, expected: bool) -> None:
    assert is_legacy_point_no(value) is expected
