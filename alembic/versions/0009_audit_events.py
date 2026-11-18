"""Add persistent audit events.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_payments_order_id_id", "payments", ["order_id", "id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_type", sa.String(12), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["order_id", "payment_id"], ["payments.order_id", "payments.id"],
            name="fk_audit_events_order_payment", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "actor_type IN ('CUSTOMER', 'STAFF', 'SYSTEM')",
            name="ck_audit_events_actor_type",
        ),
        sa.CheckConstraint(
            "(actor_type = 'STAFF' AND actor_user_id IS NOT NULL) OR "
            "(actor_type IN ('CUSTOMER', 'SYSTEM') AND actor_user_id IS NULL)",
            name="ck_audit_events_actor_user",
        ),
    )
    op.create_index("ix_audit_events_order_created_id", "audit_events", ["order_id", "created_at", "id"])


def downgrade():
    op.drop_index("ix_audit_events_order_created_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_constraint("uq_payments_order_id_id", "payments", type_="unique")
