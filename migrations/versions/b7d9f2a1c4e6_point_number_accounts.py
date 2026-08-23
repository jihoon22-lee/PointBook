"""point number accounts

Revision ID: b7d9f2a1c4e6
Revises: 96588aa65d2d
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa


revision = "b7d9f2a1c4e6"
down_revision = "96588aa65d2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("people", sa.Column("point_no", sa.String(length=8), nullable=True))
    op.add_column(
        "people",
        sa.Column(
            "account_type",
            sa.String(length=20),
            nullable=False,
            server_default="person",
        ),
    )
    op.execute("UPDATE people SET point_no = printf('L%07d', id) WHERE point_no IS NULL")

    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.drop_constraint("uq_person_key", type_="unique")
        batch_op.alter_column(
            "personal_no",
            existing_type=sa.String(length=50),
            nullable=True,
        )
        batch_op.alter_column(
            "point_no",
            existing_type=sa.String(length=8),
            nullable=False,
        )
        batch_op.create_index("ix_people_point_no", ["point_no"], unique=True)
        batch_op.create_index("ix_people_account_type", ["account_type"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.drop_index("ix_people_account_type")
        batch_op.drop_index("ix_people_point_no")
        batch_op.alter_column(
            "personal_no",
            existing_type=sa.String(length=50),
            nullable=False,
        )
        batch_op.create_unique_constraint("uq_person_key", ["personal_no", "name"])

    op.drop_column("people", "account_type")
    op.drop_column("people", "point_no")
