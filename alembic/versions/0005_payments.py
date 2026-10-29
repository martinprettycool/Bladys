"""Add transfer payments; refuse to invent approval history for advanced orders."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Executable in online migrations and generated offline PostgreSQL SQL.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM orders WHERE status IN ('PREPARANDO', 'LISTO', 'ENVIADO')) THEN
                RAISE EXCEPTION 'Resolve legacy advanced orders explicitly before adding payments';
            END IF;
        END $$;
    """))
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDIENTE_COMPROBANTE"),
        sa.Column("proof_reference", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('PENDIENTE_COMPROBANTE', 'PENDIENTE_VERIFICACION', 'APROBADO', 'RECHAZADO')", name="ck_payments_status"),
    )
    op.execute(sa.text("INSERT INTO payments (order_id) SELECT id FROM orders"))


def downgrade():
    op.drop_table("payments")
