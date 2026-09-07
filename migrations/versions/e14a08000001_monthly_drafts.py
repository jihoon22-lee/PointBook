"""인증된 월간 초안과 낙관적 버전.

Revision ID: e14a08000001
Revises: d14a06000001
"""

import sqlalchemy as sa
from alembic import op

revision = "e14a08000001"
down_revision = "d14a06000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monthly_drafts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("request_key", sa.String(64), unique=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("month", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("review_state", sa.String(20), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("result_url", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_draft_version"),
        sa.CheckConstraint("status IN ('active','confirmed','deleted','expired')", name="ck_draft_status"),
    )
    for column in ("owner_id", "status", "expires_at"):
        op.create_index(f"ix_monthly_drafts_{column}", "monthly_drafts", [column])


def downgrade():
    raise RuntimeError("초안을 보존한 뒤 호환 이미지·검증 사본으로 복원하세요.")
