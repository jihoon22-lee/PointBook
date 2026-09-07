"""읽기 전용 정합성 검사와 승인된 계산 필드 복구. 원문 입력과 unknown 역사는 바꾸지 않는다."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.db import current_database_path
from app.models import BalanceAdjustment, BalanceRecord, Person
from app.services.backup import BackupError, _atomic_json, _fsync, _sync_directory, copy_database
from app.services.dates import validate_month
from app.services.history import preserve_revision, record_data
from app.services.ledger import (
    LedgerConflict,
    LedgerPlan,
    _ensure_original,
    _reason,
    add_operation,
    ledger_version,
    matches_plan,
    sign_plan,
    validate_request_key,
)


@dataclass
class IntegrityReport:
    version: int
    issues: list[str] = field(default_factory=list)
    record_changes: list[dict[str, Any]] = field(default_factory=list)
    current_changes: list[dict[str, Any]] = field(default_factory=list)
    unknown_history: int = 0
    record_count: int = 0
    adjustment_count: int = 0
    blocking: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.issues and not self.blocking


def inspect_ledger(db: Session) -> IntegrityReport:
    report = IntegrityReport(version=ledger_version(db))
    if db.execute(text("PRAGMA integrity_check")).scalars().all() != ["ok"]:
        report.blocking.append(
            "저장 파일 무결성 검사에 실패했습니다. 보호된 사본에서 복구를 검토하세요."
        )
        return report
    if db.execute(text("PRAGMA foreign_key_check")).first():
        report.blocking.append("장부 연결이 잘못된 행이 있습니다. 자동 계산 복구를 할 수 없습니다.")
        return report
    records = list(db.scalars(select(BalanceRecord).options(joinedload(BalanceRecord.snapshot))))
    adjustments = list(db.scalars(select(BalanceAdjustment)))
    people = {p.id: p for p in db.scalars(select(Person))}
    report.record_count = len(records)
    report.adjustment_count = len(adjustments)
    groups: dict[int, list[tuple[str, datetime, int, int, BalanceRecord | BalanceAdjustment]]] = {}
    for record in records:
        if record.provenance not in {"observed", "manual_correction"}:
            report.unknown_history += 1
        try:
            validate_month(record.snapshot.month)
            if not isinstance(json.loads(record.profile_data), dict):
                raise TypeError
        except (ValueError, TypeError):
            report.blocking.append(f"기록 {record.id}: 월 또는 당시 정보 형식을 확인해야 합니다.")
        groups.setdefault(record.person_id, []).append(
            (
                record.snapshot.month,
                record.observed_at or datetime.min,  # noqa: DTZ901 - SQLite의 naive UTC 정렬 기준
                record.id,
                0,
                record,
            )
        )
    for adjustment in adjustments:
        try:
            validate_month(adjustment.month)
        except ValueError:
            report.blocking.append(f"보정 {adjustment.id}: 관측 월 형식을 확인해야 합니다.")
        groups.setdefault(adjustment.person_id, []).append(
            (adjustment.month, adjustment.observed_at, adjustment.id, 1, adjustment)
        )
    for person_id, events in groups.items():
        previous: int | None = None
        expected_current: dict[str, int] = {}
        for month, at, identifier, kind, event in sorted(events, key=lambda item: item[:4]):
            if isinstance(event, BalanceAdjustment):
                previous = event.total
                expected_current = {"carry_balance": event.total, "amount": 0}
                continue
            total = event.carry_balance + event.amount
            usage = previous - event.carry_balance if previous is not None else 0
            if event.total != total or event.usage != usage:
                before = record_data(event)
                after = {**before, "total": total, "usage": usage, "version": event.version + 1}
                report.record_changes.append(
                    {
                        "record_id": event.id,
                        "person_id": person_id,
                        "before": before,
                        "after": after,
                    }
                )
                report.issues.append(
                    f"{month} 기록 {identifier}: 총잔액 또는 직전 관측 기준 순사용이 다릅니다."
                )
            previous = total
            expected_current = {"carry_balance": event.carry_balance, "amount": event.amount}
        person = people.get(person_id)
        if (
            person
            and expected_current
            and (
                person.current_carry_balance != expected_current["carry_balance"]
                or person.current_amount != expected_current["amount"]
            )
        ):
            report.current_changes.append(
                {
                    "person_id": person_id,
                    "before": {
                        "carry_balance": person.current_carry_balance,
                        "amount": person.current_amount,
                        "version": person.version,
                    },
                    "after": {**expected_current, "version": person.version + 1},
                }
            )
            report.issues.append(f"인원 {person_id}: 현재 잔액이 마지막 유효 관측과 다릅니다.")
    if ledger_version(db) != report.version:
        raise LedgerConflict("검사 중 다른 작업이 반영되었습니다. 다시 검사하세요.")
    return report


def repair_plan(db: Session, *, reason: str, request_key: str) -> LedgerPlan:
    validate_request_key(request_key)
    report = inspect_ledger(db)
    if report.blocking:
        raise ValueError(
            "구조·월·당시 정보 오류는 자동 계산 복구 대상이 아닙니다. 별도 정정/복원을 검토하세요."
        )
    if not report.record_changes and not report.current_changes:
        raise ValueError("계산 필드 복구 대상이 없습니다.")
    plan = LedgerPlan(
        kind="repair",
        request_key=request_key,
        payload={"reason": _reason(reason), "scope": "calculated_fields"},
        base_version=report.version,
        changes=report.record_changes,
    )
    # IDs와 현재 전후도 서명에 포함한다. unknown profile/status는 계획에 넣지 않는다.
    plan.current_changes = report.current_changes
    return sign_plan(plan)


def preserve_before_repair() -> Path:
    """계산이 틀린 원본도 일관된 SQLite 사본으로 격리 보존한다. 정상 백업으로 오인하지 않는다."""
    source = current_database_path()
    directory = source.parent / "repair-preserved"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / f"before-repair-{uuid.uuid4().hex}.db"
    try:
        copy_database(source, destination)
        with closing(
            sqlite3.connect(destination.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("복구 전 사본의 저장 무결성을 확인할 수 없습니다.")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise BackupError("복구 전 사본의 연결 무결성을 확인할 수 없습니다.")
        _fsync(destination)
        with destination.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        _atomic_json(
            destination.with_suffix(".json"),
            {
                "purpose": "before-calculated-field-repair",
                "sha256": digest,
                "ledger_status": "requires_repair",
                "automatic_restore": False,
            },
        )
        _sync_directory(directory)
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        destination.with_suffix(".json").unlink(missing_ok=True)
        raise


def apply_repair(db: Session, plan: LedgerPlan, token: str, actor_id: int) -> str:
    if not matches_plan(token, plan) or ledger_version(db) != plan.base_version:
        raise LedgerConflict("검사 이후 장부가 변경되었습니다. 복구 계획을 다시 검토하세요.")
    preserve_before_repair()
    operation = add_operation(
        db,
        request_key=plan.request_key,
        kind="repair",
        payload=plan.payload,
        actor_id=actor_id,
        reason=plan.payload["reason"],
        details={"changes": plan.changes, "current_changes": plan.current_changes},
        result_url=f"/ledger/operations/{plan.request_key}",
    )
    for change in plan.changes:
        record = db.get(BalanceRecord, change["record_id"])
        if record is None:
            raise LedgerConflict("복구 대상이 변경되었습니다.")
        _ensure_original(db, record)
        record.total = change["after"]["total"]
        record.usage = change["after"]["usage"]
        record.version = change["after"]["version"]
        record.snapshot.version += 1
        preserve_revision(db, record, operation.id, source="calculated_field_repair")
    for change in plan.current_changes:
        person = db.get(Person, change["person_id"])
        if person is None:
            raise LedgerConflict("현재 잔액 복구 대상이 변경되었습니다.")
        person.current_carry_balance = change["after"]["carry_balance"]
        person.current_amount = change["after"]["amount"]
        person.version = change["after"]["version"]
    db.flush()
    if not inspect_ledger(db).healthy:
        raise ValueError("복구 후 정합성 검사에 실패했습니다. 변경을 롤백합니다.")
    return operation.result_url
