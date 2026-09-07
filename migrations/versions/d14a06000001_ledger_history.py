"""월별 당시 정보·원본 revision·명시적 보정과 감사 원장.

Revision ID: d14a06000001
Revises: c14a04000001
"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "d14a06000001"
down_revision = "c14a04000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("people", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("monthly_snapshots", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("monthly_snapshots", sa.Column("status", sa.String(20), nullable=False, server_default="closed"))
    op.add_column("balance_records", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("balance_records", sa.Column("note", sa.Text(), nullable=False, server_default=""))
    op.add_column("balance_records", sa.Column("profile_data", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("balance_records", sa.Column("provenance", sa.String(30), nullable=False, server_default="unknown"))
    op.add_column("balance_records", sa.Column("observed_at", sa.DateTime(), nullable=True))
    op.create_table("ledger_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1 AND version >= 1", name="ck_ledger_state"))
    op.execute("INSERT INTO ledger_state(id,version) VALUES (1,1)")
    op.create_table("ledger_operations",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("request_key",sa.String(64),nullable=False,unique=True),
        sa.Column("payload_hash",sa.String(64),nullable=False),
        sa.Column("kind",sa.String(30),nullable=False),
        sa.Column("actor_id",sa.Integer(),sa.ForeignKey("admin_users.id"),nullable=True),
        sa.Column("reason",sa.Text(),nullable=False),
        sa.Column("detail_json",sa.Text(),nullable=False),
        sa.Column("result_url",sa.String(200),nullable=False),
        sa.Column("created_at",sa.DateTime(),nullable=False))
    op.create_table("balance_revisions",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("record_id",sa.Integer(),sa.ForeignKey("balance_records.id"),nullable=False),
        sa.Column("version",sa.Integer(),nullable=False),
        sa.Column("operation_id",sa.Integer(),sa.ForeignKey("ledger_operations.id"),nullable=True),
        sa.Column("data_json",sa.Text(),nullable=False),
        sa.Column("source",sa.String(30),nullable=False),
        sa.Column("preserved_at",sa.DateTime(),nullable=False),
        sa.UniqueConstraint("record_id","version",name="uq_record_revision"))
    op.create_index("ix_balance_revisions_record_id","balance_revisions",["record_id"])
    op.create_table("balance_adjustments",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("person_id",sa.Integer(),sa.ForeignKey("people.id"),nullable=False),
        sa.Column("month",sa.String(7),nullable=False),
        sa.Column("total",sa.Integer(),nullable=False),
        sa.Column("profile_data",sa.Text(),nullable=False),
        sa.Column("note",sa.Text(),nullable=False),
        sa.Column("operation_id",sa.Integer(),sa.ForeignKey("ledger_operations.id"),nullable=False),
        sa.Column("observed_at",sa.DateTime(),nullable=False),
        sa.CheckConstraint("total >= 0 AND typeof(total) = 'integer'",name="ck_adjustment_total"))
    op.create_index("ix_balance_adjustments_person_id","balance_adjustments",["person_id"])
    op.create_index("ix_balance_adjustments_month","balance_adjustments",["month"])
    bind=op.get_bind()
    # 이관 시 master 정보는 보조 표시용으로만 고정한다. 당시 사실/원래 작성자를 만들지 않는다.
    profiles={}
    for row in bind.execute(sa.text("SELECT p.*,t.name AS team_name,t.color AS team_color FROM people p LEFT JOIN teams t ON t.id=p.team_id")).mappings():
        profile={key:row[key] for key in ("point_no","personal_no","name","grade","status","account_type","team_id","team_name","team_color")}
        profiles[row["id"]]=profile
        bind.execute(sa.text("UPDATE balance_records SET profile_data=:profile,provenance='master_at_migration' WHERE person_id=:person_id"),
            {"profile":json.dumps(profile,ensure_ascii=False,sort_keys=True),"person_id":row["id"]})
    preserved=datetime.now(UTC).replace(tzinfo=None)
    revisions=[]
    for row in bind.execute(sa.text("SELECT r.*,s.month FROM balance_records r JOIN monthly_snapshots s ON s.id=r.snapshot_id")).mappings():
        data={key:row[key] for key in ("carry_balance","amount","usage","total","note","provenance","month")}
        data.update(profile=profiles[row["person_id"]],observed_at=None,version=1)
        revisions.append({"record_id":row["id"],"version":1,"operation_id":None,
            "data_json":json.dumps(data,ensure_ascii=False,sort_keys=True),"source":"migration_baseline","preserved_at":preserved})
        if len(revisions)>=1000:
            bind.execute(sa.text("INSERT INTO balance_revisions(record_id,version,operation_id,data_json,source,preserved_at) VALUES (:record_id,:version,:operation_id,:data_json,:source,:preserved_at)"),revisions)
            revisions=[]
    if revisions:
        bind.execute(sa.text("INSERT INTO balance_revisions(record_id,version,operation_id,data_json,source,preserved_at) VALUES (:record_id,:version,:operation_id,:data_json,:source,:preserved_at)"),revisions)
    for table in ("ledger_operations","balance_revisions","balance_adjustments"):
        for verb in ("UPDATE","DELETE"):
            op.execute(f"CREATE TRIGGER immutable_{table}_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable audit record'); END")


def downgrade():
    # 감사 이력을 버리는 자동 다운그레이드는 허용하지 않는다. 검증된 사전 사본과 이전 이미지로 복원한다.
    raise RuntimeError("장부 감사 이력 이전은 되돌릴 수 없습니다. 검증된 사전 백업과 호환 이미지로 복원하세요.")
