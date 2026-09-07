"""현재 인원 정보와 초기 잔액 관측. 과거 장부는 변경하지 않는다."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import BalanceAdjustment, BalanceRecord, LedgerOperation, Person, Team, utcnow
from app.services.backup import backup_database
from app.services.dates import current_month, validate_month
from app.services.history import profile_for_person
from app.services.identifiers import normalize_point_no
from app.services.ledger import (
    LedgerConflict,
    LedgerPlan,
    add_operation,
    ledger_version,
    matches_plan,
    sign_plan,
    validate_request_key,
)
from app.services.validation import parse_money

PROFILE_FIELDS = ("point_no", "personal_no", "name", "account_type", "grade", "team_id", "status")


def _short_text(value: str, label: str, *, required: bool = False) -> str:
    text = value.strip()
    if (required and not text) or len(text) > 50:
        raise ValueError(f"{label}: {'1~50' if required else '50자 이하의'} 글자로 입력해 주세요.")
    return text


def profile_payload(values: dict[str, str], person_id: int | None) -> dict[str, Any]:
    """재전송 대조가 DB 변경 이후에도 가능한 정규화 입력. DB에 쓰지 않는다."""
    account_type = values.get("account_type", "")
    status = values.get("status", "")
    if account_type not in {"person", "shared"}:
        raise ValueError("계정 구분은 일반 인원 또는 공용 계정이어야 합니다.")
    if status not in {"active", "inactive"}:
        raise ValueError("상태는 재직 또는 비재직이어야 합니다.")
    if account_type == "shared" and status != "active":
        raise ValueError("공용 계정은 항상 재직 상태여야 합니다.")
    point_no = normalize_point_no(values.get("point_no", ""))
    name = _short_text(values.get("name", ""), "이름", required=True)
    personal_no = _short_text(
        values.get("personal_no", ""), "개인번호", required=account_type == "person"
    )
    grade = _short_text(values.get("grade", ""), "계급")
    raw_team = values.get("team_id", "")
    if raw_team and (not re.fullmatch(r"[0-9]{1,18}", raw_team) or int(raw_team) < 1):
        raise ValueError("올바른 팀을 선택해 주세요.")
    payload: dict[str, Any] = {
        "person_id": person_id,
        "point_no": point_no,
        "personal_no": personal_no or None,
        "name": name,
        "account_type": account_type,
        "grade": grade if account_type == "person" else "",
        "team_id": int(raw_team) if raw_team else None,
        "status": status,
    }
    if person_id is None:
        payload.update(
            carry_balance=parse_money(values.get("carry_balance", ""), label="초기 이월 잔액"),
            amount=parse_money(values.get("amount", ""), label="초기 추가 금액"),
            month=validate_month(values.get("initial_month", "")),
        )
    elif "carry_balance" in values or "amount" in values:
        raise ValueError(
            "기본 정보 수정에서는 금액을 변경할 수 없습니다. 현재 잔액 보정 또는 대상 월 정정을 이용하세요."
        )
    reason = values.get("reason", "").strip()
    if not reason or len(reason) > 1000:
        raise ValueError("변경 사유를 1~1000자로 입력해 주세요.")
    payload["reason"] = reason
    return payload


def _version(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,17}", value):
        raise LedgerConflict("기준 버전이 없습니다. 화면을 다시 열어 변경 내용을 검토해 주세요.")
    return int(value)


def prepare_profile(
    db: Session,
    *,
    payload: dict[str, Any],
    request_key: str,
    base_version: str,
    person_version: str,
) -> LedgerPlan:
    validate_request_key(request_key)
    base = _version(base_version)
    if ledger_version(db) != base:
        raise LedgerConflict(
            "다른 작업으로 장부 기준이 변경되었습니다. 새 기준으로 변경 내용을 다시 검토하세요."
        )
    person = (
        db.scalar(
            select(Person).options(joinedload(Person.team)).where(Person.id == payload["person_id"])
        )
        if payload["person_id"] is not None
        else None
    )
    if payload["person_id"] is not None:
        if person is None:
            raise ValueError("대상 인원을 찾을 수 없습니다.")
        if person.version != _version(person_version):
            raise LedgerConflict(
                "다른 작업으로 인원 정보가 변경되었습니다. 새 기준으로 다시 검토하세요."
            )
    elif payload["month"] != current_month():
        raise LedgerConflict("초기 잔액 관측 월이 변경되었습니다. 새 기준으로 다시 검토하세요.")
    duplicate = db.scalar(select(Person.id).where(Person.point_no == payload["point_no"]))
    if duplicate is not None and duplicate != payload["person_id"]:
        raise ValueError(f"포인트번호 {payload['point_no']} 은(는) 이미 등록된 인원입니다.")
    team = db.get(Team, payload["team_id"]) if payload["team_id"] is not None else None
    if payload["team_id"] is not None and team is None:
        raise ValueError("선택한 팀이 존재하지 않습니다. 팀을 다시 선택해 주세요.")
    after = {field: payload[field] for field in PROFILE_FIELDS}
    after.update(team_name=team.name if team else "", team_color=team.color if team else "#9aa3ad")
    before = profile_for_person(person) if person else None
    current = (
        {
            "carry_balance": person.current_carry_balance,
            "amount": person.current_amount,
            "total": person.current_carry_balance + person.current_amount,
            "version": person.version,
        }
        if person
        else {}
    )
    total = payload["carry_balance"] + payload["amount"] if person is None else current["total"]
    next_current = (
        {**current, "version": person.version + 1}
        if person
        else {"carry_balance": total, "amount": 0, "total": total, "version": 1}
    )
    record_count = (
        db.scalar(select(func.count(BalanceRecord.id)).where(BalanceRecord.person_id == person.id))
        if person
        else 0
    )
    adjustment_count = (
        db.scalar(
            select(func.count(BalanceAdjustment.id)).where(BalanceAdjustment.person_id == person.id)
        )
        if person
        else 0
    )
    return sign_plan(
        LedgerPlan(
            kind="profile",
            request_key=request_key,
            payload=payload,
            base_version=base,
            changes=[
                {
                    "person_id": payload["person_id"],
                    "before": before,
                    "after": after,
                    "record_count": record_count,
                    "adjustment_count": adjustment_count,
                }
            ],
            current_before=current,
            current_after=next_current,
        )
    )


def identity_changed(plan: LedgerPlan) -> bool:
    change = plan.changes[0]
    return bool(
        change["before"]
        and any(
            change["before"][key] != change["after"][key] for key in ("point_no", "account_type")
        )
    )


def apply_profile(
    db: Session, plan: LedgerPlan, token: str, actor_id: int, *, identity_confirmed: bool
) -> LedgerOperation:
    """BEGIN IMMEDIATE 내에서 다시 계산한 계획만 적용한다. commit은 호출자 책임이다."""
    if not matches_plan(token, plan) or ledger_version(db) != plan.base_version:
        raise LedgerConflict("입력이나 기준 상태가 변경되었습니다. 변경 내용을 다시 검토하세요.")
    if identity_changed(plan) and not identity_confirmed:
        raise ValueError("포인트번호·계정 유형 변경의 영향 확인에 체크해 주세요.")
    if backup_database() is None:
        raise ValueError("변경 전 검증 백업을 만들 수 없습니다. 백업 상태를 확인하세요.")
    after = plan.changes[0]["after"]
    person = (
        db.get(Person, plan.payload["person_id"]) if plan.payload["person_id"] is not None else None
    )
    creating = person is None
    if creating:
        person = Person(
            **{field: after[field] for field in PROFILE_FIELDS},
            current_carry_balance=plan.current_after["total"],
            current_amount=0,
            version=1,
        )
        db.add(person)
        db.flush()
    else:
        assert person is not None
        if person.version != plan.current_before["version"]:
            raise LedgerConflict("인원 정보가 변경되었습니다. 다시 검토하세요.")
        for field in PROFILE_FIELDS:
            setattr(person, field, after[field])
        person.version += 1
    assert person is not None
    operation = add_operation(
        db,
        request_key=plan.request_key,
        kind="profile",
        payload=plan.payload,
        actor_id=actor_id,
        reason=plan.payload["reason"],
        details={
            "person_id": person.id,
            "changes": plan.changes,
            "current_before": plan.current_before,
            "current_after": plan.current_after,
            "initial_observation": creating,
        },
        result_url=f"/people/{person.id}",
    )
    if creating:
        db.add(
            BalanceAdjustment(
                person_id=person.id,
                month=plan.payload["month"],
                total=plan.current_after["total"],
                profile_data=json.dumps(after, ensure_ascii=False, sort_keys=True),
                note="계정 등록 초기 잔액",
                operation_id=operation.id,
                observed_at=utcnow(),
            )
        )
    return operation
