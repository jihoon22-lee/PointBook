"""관리자 세션 버전과 장부 입력 도메인 제약.

Revision ID: c14a04000001
Revises: b7d9f2a1c4e6
"""

from alembic import op
import sqlalchemy as sa

revision = "c14a04000001"
down_revision = "b7d9f2a1c4e6"
branch_labels = None
depends_on = None

PEOPLE = {
    "ck_people_status": "status IN ('active', 'inactive')",
    "ck_people_account_type": "account_type IN ('person', 'shared')",
    "ck_people_shared_active": "account_type != 'shared' OR status = 'active'",
    "ck_people_carry": "current_carry_balance >= 0 AND typeof(current_carry_balance) = 'integer'",
    "ck_people_amount": "current_amount >= 0 AND typeof(current_amount) = 'integer'",
}
RECORDS = {
    "ck_records_carry": "carry_balance >= 0 AND typeof(carry_balance) = 'integer'",
    "ck_records_amount": "amount >= 0 AND typeof(amount) = 'integer'",
}


def upgrade() -> None:
    # Check everything before DDL; never silently repair existing ledger values.
    bind = op.get_bind()
    for table, constraints in (("people", PEOPLE), ("balance_records", RECORDS)):
        for expression in constraints.values():
            if bind.execute(
                sa.text(f"SELECT 1 FROM {table} WHERE NOT ({expression}) LIMIT 1")
            ).first():
                raise RuntimeError("기존 DB의 도메인 값이 올바르지 않아 이전을 중단했습니다.")
    op.add_column(
        "admin_users", sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1")
    )
    for table, constraints in (("people", PEOPLE), ("balance_records", RECORDS)):
        with op.batch_alter_table(table) as batch:
            for name, expression in constraints.items():
                batch.create_check_constraint(name, expression)


def downgrade() -> None:
    for table, constraints in (("balance_records", RECORDS), ("people", PEOPLE)):
        with op.batch_alter_table(table) as batch:
            for name in constraints:
                batch.drop_constraint(name, type_="check")
    op.drop_column("admin_users", "auth_version")
