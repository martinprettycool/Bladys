"""Introduce product variants while preserving existing order items.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"],
            name="fk_product_variants_product_id_products", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("price >= 0", name="ck_product_variants_price_nonnegative"),
    )
    op.execute(sa.text("""
        INSERT INTO product_variants (product_id, name, price, is_available)
        SELECT id, 'Regular', price, is_available FROM products
    """))
    op.add_column("order_items", sa.Column("variant_id", sa.Integer(), nullable=True))
    op.execute(sa.text("""
        UPDATE order_items AS item
        SET variant_id = variant.id
        FROM product_variants AS variant
        WHERE variant.product_id = item.product_id
          AND variant.name = 'Regular'
    """))
    op.alter_column("order_items", "variant_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "fk_order_items_variant_id_product_variants",
        "order_items", "product_variants", ["variant_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.drop_column("order_items", "product_id")
    op.drop_constraint("ck_products_price_nonnegative", "products", type_="check")
    op.drop_column("products", "price")


def downgrade():
    raise NotImplementedError(
        "Downgrade to 0001 requires an explicit policy for preserving multiple "
        "variants and their prices; automatic downgrade is disabled."
    )
