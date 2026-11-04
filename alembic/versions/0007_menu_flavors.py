"""Menu metadata and historical item flavor snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("category", sa.String(20), nullable=True))
        batch.create_check_constraint("ck_products_category", "category IN ('PIZZA', 'HAMBURGUESA', 'ALITAS', 'PAPITAS')")
    with op.batch_alter_table("product_variants") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("max_flavors", sa.Integer(), nullable=True))
        batch.create_check_constraint("ck_product_variants_max_flavors_positive", "max_flavors > 0")
    op.add_column("order_items", sa.Column("selected_flavors", sa.JSON(), nullable=False, server_default="[]"))


def downgrade():
    raise NotImplementedError("Automatic downgrade would discard historical flavor selections.")
