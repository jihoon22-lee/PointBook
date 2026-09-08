"""서버 초안의 원문·행 ID·버전·보관 계약. 저장은 장부나 인원을 변경하지 않는다."""

import json
import re
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.config import get_settings
from app.models import MonthlyDraft, utcnow
from app.services.ledger import ledger_version, start_write, validate_request_key
from app.services.parsing import MAX_REQUEST_ROWS, RawRequestRow
from app.services.review import (
    carry_values,
    deactivated_from_form,
    matches_token,
    raw_rows_from_form,
    review_rows,
)

MAX_DRAFT_BYTES = 16 * 1024 * 1024
SOURCES = {"manual", "paste", "image", "xlsx"}


class DraftError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def draft_payload(
    month: str,
    rows: list[RawRequestRow],
    *,
    deactivated: dict[str, str] | None = None,
    expected_count: str = "",
    expected_amount: str = "",
    source: str = "manual",
    review_token: str = "",
) -> dict[str, Any]:
    if source not in SOURCES:
        raise DraftError("입력 출처를 확인해 주세요.")
    if len(rows) > MAX_REQUEST_ROWS or len(month) > 20:
        raise DraftError("초안의 월·행 수 범위를 확인해 주세요.")
    if any(len(value) > 20_000 for row in rows for value in asdict(row).values()):
        raise DraftError("초안 셀의 원문 길이 한도를 넘었습니다.", 413)
    value = {
        "month": month,
        "rows": [asdict(row) for row in rows],
        "deactivated": deactivated or {},
        "expected_count": expected_count,
        "expected_amount": expected_amount,
        "source": source,
        "review_token": review_token,
    }
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_DRAFT_BYTES:
        raise DraftError(
            "초안이 저장 한도(16MB)를 넘었습니다. 원문 길이와 행 수를 확인해 주세요.", 413
        )
    return value


def payload_from_form(form: FormData) -> dict[str, Any]:
    return draft_payload(
        str(form.get("month", "")),
        raw_rows_from_form(form),
        deactivated=deactivated_from_form(form),
        expected_count=str(form.get("expected_count", "")),
        expected_amount=str(form.get("expected_amount", "")),
        source=str(form.get("input_source", "manual")),
        review_token=str(form.get("review_token", "")),
    )


def parse_version(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", value) or int(value) < 1:
        raise DraftError("초안 버전을 확인할 수 없습니다. 서버 초안을 다시 열어 주세요.", 409)
    return int(value)


def get_draft(db: Session, draft_id: str, owner_id: int) -> MonthlyDraft:
    draft = db.scalar(
        select(MonthlyDraft).where(MonthlyDraft.id == draft_id, MonthlyDraft.owner_id == owner_id)
    )
    if draft is None:
        raise DraftError("초안을 찾을 수 없습니다.", 404)
    if draft.status in {"deleted", "expired"} or draft.expires_at <= utcnow():
        raise DraftError(
            "삭제되었거나 보관 기간이 지난 초안입니다. 화면에 남은 입력은 새 초안으로 저장할 수 있습니다.",
            410,
        )
    return draft


def _prune_expired(db: Session) -> None:
    db.execute(
        update(MonthlyDraft)
        .where(
            MonthlyDraft.expires_at <= utcnow(), MonthlyDraft.status.in_(["active", "confirmed"])
        )
        .values(status="expired", payload_json="{}", version=MonthlyDraft.version + 1)
    )


def save_draft(
    db: Session,
    *,
    owner_id: int,
    payload: dict[str, Any],
    request_key: str,
    draft_id: str = "",
    version: str = "",
) -> MonthlyDraft:
    validate_request_key(request_key)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > MAX_DRAFT_BYTES:
        raise DraftError("초안 저장 한도를 넘었습니다.", 413)
    try:
        start_write(db)
        rows = [RawRequestRow(**row) for row in payload["rows"]]
        review = review_rows(
            db, payload["month"], rows, payload["expected_count"], payload["expected_amount"]
        )
        payload = {**payload, "rows": [asdict(row) for row in review.raw_rows]}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode()) > MAX_DRAFT_BYTES:
            raise DraftError("초안 저장 한도를 넘었습니다.", 413)
        _prune_expired(db)
        if draft_id:
            draft = get_draft(db, draft_id, owner_id)
            if draft.status != "active":
                raise DraftError("이미 확정된 초안입니다. 변경은 정정 경로를 이용해 주세요.", 409)
            if draft.version != parse_version(version) or draft.request_key != request_key:
                raise DraftError(
                    "서버 초안이 변경되었습니다. 다른 탭·기기 또는 이전 저장 결과와 대조하거나 이 입력을 새 초안으로 저장하세요.",
                    409,
                )
            draft.version += 1
        else:
            existing = db.scalar(
                select(MonthlyDraft).where(MonthlyDraft.request_key == request_key)
            )
            if existing:
                if (
                    existing.owner_id == owner_id
                    and existing.payload_json == encoded
                    and existing.status == "active"
                ):
                    db.rollback()
                    return existing
                raise DraftError("같은 작업의 초안이 이미 있습니다. 서버 초안을 확인하세요.", 409)
            count = (
                db.scalar(
                    select(func.count(MonthlyDraft.id)).where(
                        MonthlyDraft.owner_id == owner_id, MonthlyDraft.status == "active"
                    )
                )
                or 0
            )
            if count >= get_settings().draft_max_active:
                raise DraftError(
                    "보관 가능한 초안 수에 도달했습니다. 사용하지 않는 초안을 삭제해 주세요.", 409
                )
            draft = MonthlyDraft(
                id=uuid.uuid4().hex,
                owner_id=owner_id,
                request_key=request_key,
                version=1,
                status="active",
                result_url="",
            )
            db.add(draft)
        _carries, errors = (
            carry_values(review, payload["deactivated"]) if not review.errors else ({}, {})
        )
        draft.review_state = (
            "incomplete"
            if review.errors or errors
            else (
                "reviewed"
                if matches_token(payload["review_token"], review.digest)
                else "needs_review"
            )
        )
        draft.base_version = ledger_version(db)
        draft.month = payload["month"]
        draft.payload_json = encoded
        draft.updated_at = utcnow()
        draft.expires_at = draft.updated_at + timedelta(days=get_settings().draft_keep_days)
        db.commit()
        return draft
    except Exception:
        db.rollback()
        raise


def verify_draft_for_confirm(
    db: Session, *, draft_id: str, owner_id: int, version: str, request_key: str
) -> MonthlyDraft:
    draft = get_draft(db, draft_id, owner_id)
    if (
        draft.status != "active"
        or draft.version != parse_version(version)
        or draft.request_key != request_key
    ):
        raise DraftError(
            "초안이 다른 곳에서 변경되었거나 이미 확정되었습니다. 다시 검토해 주세요.", 409
        )
    return draft


def confirm_draft(draft: MonthlyDraft, payload: dict[str, Any], result_url: str) -> None:
    draft.payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    draft.status = "confirmed"
    draft.version += 1
    draft.review_state = "confirmed"
    draft.result_url = result_url
    draft.updated_at = utcnow()


def delete_draft(db: Session, *, draft_id: str, owner_id: int, version: str) -> None:
    try:
        start_write(db)
        draft = get_draft(db, draft_id, owner_id)
        if draft.version != parse_version(version):
            raise DraftError("다른 곳에서 변경된 초안입니다. 목록을 다시 확인해 주세요.", 409)
        if draft.status != "active":
            raise DraftError("확정된 작업은 초안 삭제 대상이 아닙니다.", 409)
        draft.status, draft.payload_json = "deleted", "{}"
        draft.version += 1
        db.commit()
    except Exception:
        db.rollback()
        raise


def draft_state(draft: MonthlyDraft) -> dict[str, Any]:
    return {
        "draft_id": draft.id,
        "draft_version": draft.version,
        "request_key": draft.request_key,
        "draft_saved_at": draft.updated_at.isoformat() + "Z",
        "draft_expires_at": draft.expires_at.isoformat() + "Z",
        "draft_review_state": draft.review_state,
    }


def clean_expired() -> None:
    from app import db as db_module

    with db_module.SessionLocal() as db:
        _prune_expired(db)
        db.commit()


async def cleanup_loop() -> None:
    """만료 즉시 접근을 거부하고 원문은 시작 시 및 한 시간마다 정리한다."""
    import asyncio

    from app.logging import get_logger

    while True:
        await asyncio.sleep(3600)
        worker = asyncio.create_task(asyncio.to_thread(clean_expired))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise
        except Exception:  # noqa: BLE001
            get_logger().warning("만료 초안 정리에 실패했습니다. 다음 주기에 재시도합니다.")
