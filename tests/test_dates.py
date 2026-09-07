from datetime import datetime

import app.services.dates as dates_module
from app.services.dates import KST, current_month


def test_current_month_format():
    month = current_month()
    assert len(month) == 7
    assert month[4] == "-"
    assert month[:4].isdigit()
    assert month[5:].isdigit()


def test_current_month_kst_boundary(monkeypatch):
    class FakeDateTime:
        @classmethod
        def now(cls, tz):
            assert tz == KST
            return datetime(2026, 1, 1, 0, 30, tzinfo=tz)

    monkeypatch.setattr(dates_module, "datetime", FakeDateTime)
    assert current_month() == "2026-01"


def test_current_month_kst_not_utc(monkeypatch):
    class FakeDateTime:
        @classmethod
        def now(cls, tz):
            assert tz == KST
            return datetime(2026, 1, 1, 8, 30, tzinfo=tz)

    monkeypatch.setattr(dates_module, "datetime", FakeDateTime)
    assert current_month() == "2026-01"


import pytest

from app.services.dates import validate_month


@pytest.mark.parametrize("value", ["0001-01", "2026-02", "2026-12", "9999-12"])
def test_validate_month_calendar_boundaries(value):
    assert validate_month(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "0000-01",
        "2026-00",
        "2026-13",
        "2026-1",
        "26-01",
        "10000-01",
        "2026/01",
        "２０２６-01",
        "2026-０１",
        " 2026-01",
        "2026-01\n",
        "",
        "2026-01-01",
    ],
)
def test_validate_month_rejects_invalid_calendar_and_format(value):
    with pytest.raises(ValueError):
        validate_month(value)
