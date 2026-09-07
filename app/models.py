from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#4a7dbd")

    persons: Mapped[list[Person]] = relationship(back_populates="team")


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name="ck_people_status"),
        CheckConstraint("account_type IN ('person', 'shared')", name="ck_people_account_type"),
        CheckConstraint(
            "account_type != 'shared' OR status = 'active'", name="ck_people_shared_active"
        ),
        CheckConstraint(
            "current_carry_balance >= 0 AND typeof(current_carry_balance) = 'integer'",
            name="ck_people_carry",
        ),
        CheckConstraint(
            "current_amount >= 0 AND typeof(current_amount) = 'integer'", name="ck_people_amount"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    point_no: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    personal_no: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(50))
    grade: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    account_type: Mapped[str] = mapped_column(String(20), default="person", index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    current_carry_balance: Mapped[int] = mapped_column(Integer, default=0)
    current_amount: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    team: Mapped[Team | None] = relationship(back_populates="persons")
    balances: Mapped[list[BalanceRecord]] = relationship(back_populates="person")


class MonthlySnapshot(Base):
    __tablename__ = "monthly_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), default="closed", server_default="closed")

    records: Mapped[list[BalanceRecord]] = relationship(back_populates="snapshot")


class BalanceRecord(Base):
    __tablename__ = "balance_records"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "person_id", name="uq_snapshot_person"),
        CheckConstraint(
            "carry_balance >= 0 AND typeof(carry_balance) = 'integer'", name="ck_records_carry"
        ),
        CheckConstraint("amount >= 0 AND typeof(amount) = 'integer'", name="ck_records_amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("monthly_snapshots.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    carry_balance: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    profile_data: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    provenance: Mapped[str] = mapped_column(String(30), default="unknown", server_default="unknown")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    snapshot: Mapped[MonthlySnapshot] = relationship(back_populates="records")
    person: Mapped[Person] = relationship(back_populates="balances")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    auth_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class LedgerState(Base):
    __tablename__ = "ledger_state"
    __table_args__ = (CheckConstraint("id = 1 AND version >= 1", name="ck_ledger_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class LedgerOperation(Base):
    __tablename__ = "ledger_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    result_url: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BalanceRevision(Base):
    __tablename__ = "balance_revisions"
    __table_args__ = (UniqueConstraint("record_id", "version", name="uq_record_revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("balance_records.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_operations.id"), nullable=True
    )
    data_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30))
    preserved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BalanceAdjustment(Base):
    """현재 잔액의 명시적 관측. 월간 충전 기록을 덮거나 월 전체를 마감하지 않는다."""

    __tablename__ = "balance_adjustments"
    __table_args__ = (
        CheckConstraint("total >= 0 AND typeof(total) = 'integer'", name="ck_adjustment_total"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    total: Mapped[int] = mapped_column(Integer)
    profile_data: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    operation_id: Mapped[int] = mapped_column(ForeignKey("ledger_operations.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
