"""WP01 기준 규모(200/1000명×24개월)의 최종 검수·초안·보고서 비용."""

import json
import time

import pytest
from sqlalchemy import event, insert

from app import db as db_module
from app.models import BalanceRecord, BalanceRevision, MonthlySnapshot, Person, utcnow
from tests.monthly_helpers import review_fields


@pytest.mark.parametrize("population", [200, 1000])
def test_final_workflow_scale_has_bounded_queries_and_response(auth_client, db, population):
    people = []
    profiles = {}
    for index in range(1, population + 1):
        point = f"{index:08d}"
        people.append(
            {
                "id": index,
                "point_no": point,
                "personal_no": str(index),
                "name": f"합성{index:04d}",
                "account_type": "person",
                "status": "active",
                "grade": "",
                "current_carry_balance": 50,
                "current_amount": 50,
            }
        )
        profiles[index] = {
            "point_no": point,
            "personal_no": str(index),
            "name": f"합성{index:04d}",
            "account_type": "person",
            "status": "active",
            "grade": "",
            "team_id": None,
            "team_name": "",
            "team_color": "#9aa3ad",
        }
    db.execute(insert(Person), people)
    records = []
    revisions = []
    for offset in range(24):
        ordinal = 2024 * 12 + 8 + offset
        month = f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"
        snapshot_id = offset + 1
        db.execute(insert(MonthlySnapshot), {"id": snapshot_id, "month": month})
        for person_id in range(1, population + 1):
            record_id = offset * population + person_id
            data = {
                "month": month,
                "carry_balance": 50,
                "amount": 50,
                "usage": 0 if offset == 0 else 50,
                "total": 100,
                "note": "성능 합성",
                "profile": profiles[person_id],
                "provenance": "observed",
                "observed_at": None,
                "version": 1,
            }
            records.append(
                {
                    "id": record_id,
                    "snapshot_id": snapshot_id,
                    "person_id": person_id,
                    "carry_balance": 50,
                    "amount": 50,
                    "usage": data["usage"],
                    "total": 100,
                    "profile_data": json.dumps(profiles[person_id]),
                    "provenance": "observed",
                    "version": 1,
                    "note": "성능 합성",
                }
            )
            revisions.append(
                {
                    "record_id": record_id,
                    "version": 1,
                    "data_json": json.dumps(data),
                    "source": "synthetic_scale",
                    "preserved_at": utcnow(),
                }
            )
    db.execute(insert(BalanceRecord), records)
    db.execute(insert(BalanceRevision), revisions)
    db.commit()
    data = {"month": "2026-09"}
    for index in range(population):
        data.update(
            {
                f"point_no_{index}": f"{index + 1:08d}",
                f"account_type_{index}": "person",
                f"personal_no_{index}": str(index + 1),
                f"name_{index}": f"합성{index + 1:04d}",
                f"team_{index}": "",
                f"grade_{index}": "",
                f"amount_{index}": "50",
                f"carry_{index}": "50",
            }
        )
    import re

    data["csrf_token"] = re.search(
        r'name="csrf_token" value="([^"]+)"', auth_client.get("/monthly").text
    ).group(1)
    count = 0

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal count
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            count += 1

    event.listen(db_module.engine, "before_cursor_execute", capture)
    measurements = {}
    fields = {}
    try:
        for name, path, payload, budget in [
            ("review", "/monthly/review", data, 60),
            ("save", "/drafts/save", None, 20),
            ("dashboard", "/dashboard?month=2026-08&account_type=all", None, 12),
            ("export", "/dashboard/export.xlsx?month=2026-08&account_type=all", None, 10),
        ]:
            count = 0
            began = time.perf_counter()
            if name == "review":
                response = auth_client.raw_request("POST", path, data=payload)
            elif name == "save":
                response = auth_client.raw_request("POST", path, data=fields)
            else:
                response = auth_client.get(path)
            elapsed = time.perf_counter() - began
            assert response.status_code == 200
            assert count <= budget, (name, count)
            assert elapsed < 3.0, (name, elapsed)
            measurements[name] = (count, round(elapsed, 3))
            if name == "review":
                fields = review_fields(response)
        print(f"synthetic scale {population} x24: {measurements}")
    finally:
        event.remove(db_module.engine, "before_cursor_execute", capture)
