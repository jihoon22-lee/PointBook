"""변경 이력의 UTC 저장 보존과 한국 시간·금액 표시 회귀."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.models import LedgerOperation
from app.services.dates import KST
from app.template_utils import kst_datetime


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 8, 31, 20, 4, 29, tzinfo=UTC).replace(tzinfo=None), "2026-09-01 05:04:29"),
        (datetime(2026, 8, 31, 20, 4, 29, tzinfo=UTC), "2026-09-01 05:04:29"),
        (datetime(2026, 9, 1, 5, 4, 29, tzinfo=KST), "2026-09-01 05:04:29"),
        (None, "미확인"),
    ],
)
def test_korean_time_handles_utc_storage_and_already_aware_values(instant, expected):
    assert kst_datetime(instant) == expected


def add_operation(db, kind, details):
    operation = LedgerOperation(
        request_key=uuid.uuid4().hex,
        payload_hash="a" * 64,
        kind=kind,
        actor_id=1,
        reason="합성 장부 대조",
        detail_json=json.dumps(details, ensure_ascii=False),
        result_url="/ledger",
        created_at=datetime(2026, 8, 31, 20, 4, 29, tzinfo=UTC).replace(tzinfo=None),
    )
    db.add(operation)
    db.commit()
    return operation


def test_list_and_detail_show_same_korean_time_without_rewriting_history(auth_client, db):
    operation = add_operation(db, "monthly", {"month": "2026-09", "changes": []})
    before = (operation.created_at, operation.detail_json)
    listing = auth_client.get("/ledger")
    detail = auth_client.get("/ledger/operations/" + operation.request_key)
    for response in (listing, detail):
        assert response.status_code == 200
        assert "2026-09-01 05:04:29" in response.text
        assert "한국 시간" in response.text
        assert "2026-08-31 20:04:29" not in response.text
    db.refresh(operation)
    assert (operation.created_at, operation.detail_json) == before


def test_audit_record_amounts_are_formatted_but_notes_and_identifiers_are_not(auth_client, db):
    before = {
        "month": "2026-08",
        "carry_balance": 1234567,
        "amount": 1000000,
        "usage": -10000,
        "total": 2234567,
        "note": "비고 1234567\n00000111",
    }
    after = {**before, "amount": 2000000, "total": 3234567}
    operation = add_operation(db, "correction", {"changes": [{"before": before, "after": after}]})
    response = auth_client.get("/ledger/operations/" + operation.request_key)
    assert response.status_code == 200
    for amount in ("1,234,567", "1,000,000", "2,000,000", "-10,000", "2,234,567", "3,234,567"):
        assert f">{amount}<" in response.text
    assert before["note"] in response.text
    assert ">1234567<" not in response.text
    assert json.loads(operation.detail_json)["changes"][0]["before"] == before


def test_adjustment_amount_format_does_not_format_point_identifiers(auth_client, db):
    operation = add_operation(
        db,
        "adjustment",
        {
            "before": {"total": 1234567, "point_no": "00000111"},
            "after": {"total": 2234567, "point_no": "00000111"},
        },
    )
    response = auth_client.get("/ledger/operations/" + operation.request_key)
    assert response.status_code == 200
    assert ">1,234,567<" in response.text and ">2,234,567<" in response.text
    assert ">00000111<" in response.text
