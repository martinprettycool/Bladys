"""Version preconditions for customer item replacement and payment proofs."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_orders_version_positive", "orders", "version > 0")


def downgrade():
    op.drop_constraint("ck_orders_version_positive", "orders", type_="check")
    op.drop_column("orders", "version")
