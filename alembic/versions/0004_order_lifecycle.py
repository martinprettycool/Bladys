"""Restrict orders to the six MVP lifecycle states.

Revision ID: 0004
Revises: 9ef8a9c6b30a
"""
from alembic import op

revision = "0004"
down_revision = "9ef8a9c6b30a"
branch_labels = None
depends_on = None


def upgrade():
    # Existing unsupported states must be resolved explicitly before upgrading.
    op.create_check_constraint(
        "ck_orders_status",
        "orders",
        "status IN ('PENDIENTE_ACEPTACION', 'PREPARANDO', 'LISTO', "
        "'ENVIADO', 'RECHAZADO', 'CANCELADO')",
    )


def downgrade():
    op.drop_constraint("ck_orders_status", "orders", type_="check")
