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
