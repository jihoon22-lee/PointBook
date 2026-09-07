import pytest

from app.services.validation import (
    MAX_MONEY,
    MAX_TOTAL,
    parse_expected_count,
    parse_expected_total,
    parse_money,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        ("0", 0),
        ("000", 0),
        ("50,000", 50000),
        ("₩50,000원", 50000),
        (" 50000원 ", 50000),
        (MAX_MONEY, MAX_MONEY),
        ("999,999,999,999", MAX_MONEY),
        ("0" * 5000, 0),
    ],
)
def test_money_allowed_formats(value, expected):
    assert parse_money(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "-1,000",
        "-0",
        "+100",
        "12.5",
        "5O000",
        "abc",
        "1,00",
        "1,0000",
        "12,34,567",
        "1 000",
        "₩ 100",
        "100 원",
        "100원원",
        "₩₩100",
        "１２３",
        "١٢٣",
        "1_000",
        "NaN",
        "Infinity",
        "1e3",
        "100\n원",
        True,
        False,
        0.0,
        float("nan"),
        float("inf"),
        None,
        [],
        {},
        -1,
        MAX_MONEY + 1,
        str(MAX_MONEY + 1),
        "9" * 5000,
    ],
)
def test_money_rejects_entire_invalid_input(value):
    with pytest.raises(ValueError, match="이월 잔액"):
        parse_money(value, label="이월 잔액")


def test_total_range_is_distinct_from_input_range():
    assert parse_money(MAX_MONEY) + parse_money(MAX_MONEY) == MAX_TOTAL
    assert MAX_TOTAL < 2**63 - 1
    with pytest.raises(ValueError):
        parse_money(MAX_TOTAL)


@pytest.mark.parametrize("max_rows", [2, 2000])
def test_expected_total_supports_all_rows_at_individual_limit(max_rows):
    maximum = MAX_MONEY * max_rows
    assert parse_expected_total(maximum, max_rows=max_rows) == maximum
    assert parse_expected_total(f"₩{maximum:,}원", max_rows=max_rows) == maximum
    assert parse_expected_total("0", max_rows=max_rows) == 0
    with pytest.raises(ValueError, match="기대 총액"):
        parse_expected_total(maximum + 1, max_rows=max_rows)
    with pytest.raises(ValueError, match="기대 총액"):
        parse_expected_total(str(maximum + 1), max_rows=max_rows)
    with pytest.raises(ValueError):
        parse_money(maximum)


@pytest.mark.parametrize(
    "value",
    ["", "-1", "12.5", "5O000", "1,00", "1 000", True, False, 1.0, float("nan"), "9" * 5000],
)
def test_expected_total_keeps_strict_individual_money_grammar(value):
    with pytest.raises(ValueError, match="기대 총액"):
        parse_expected_total(value)


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 0), ("0", 0), (2000, 2000), ("2000", 2000), (" 0012 ", 12)]
)
def test_expected_count_accepts_only_integer_counts(value, expected):
    assert parse_expected_count(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "-1",
        "+1",
        "1.0",
        "1,000",
        "1원",
        "₩1",
        "1명",
        "١",
        "１",
        True,
        False,
        1.0,
        float("nan"),
        2001,
        "2001",
        "9" * 5000,
    ],
)
def test_expected_count_rejects_currency_noninteger_and_overflow(value):
    with pytest.raises(ValueError, match="기대 인원"):
        parse_expected_count(value)


def test_expected_count_honors_requested_row_limit():
    assert parse_expected_count("2", max_rows=2) == 2
    with pytest.raises(ValueError, match="2명 이하"):
        parse_expected_count("3", max_rows=2)
