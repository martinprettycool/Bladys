"""Carga el menú inicial sin modificar productos ni variantes existentes."""

from decimal import Decimal

from sqlalchemy import select

from bladys.database import SessionLocal
from bladys.models import Product, ProductVariant


PRODUCT_NAMES = (
    "Especial",
    "Italiana",
    "Napolitana",
    "Hawayana",
    "Tocineta",
    "Carnívora",
    "Carne Dulce",
    "Vegetariana",
)
VARIANTS = (
    ("Personal", Decimal("3.00")),
    ("Pequeña", Decimal("5.00")),
    ("Mediana", Decimal("10.00")),
    ("Familiar", Decimal("15.00")),
)


def seed_products():
    products_created = 0
    variants_created = 0
    with SessionLocal.begin() as session:
        for name in PRODUCT_NAMES:
            product = session.scalars(
                select(Product).where(Product.name == name).order_by(Product.id)
            ).first()
            if product is None:
                product = Product(name=name)
                session.add(product)
                session.flush()
                products_created += 1

            for variant_name, price in VARIANTS:
                existing_id = session.scalar(
                    select(ProductVariant.id).where(
                        ProductVariant.product_id == product.id,
                        ProductVariant.name == variant_name,
                    ).limit(1)
                )
                if existing_id is None:
                    session.add(ProductVariant(
                        product_id=product.id, name=variant_name, price=price,
                    ))
                    variants_created += 1

    print(
        f"Seed completado: {products_created} productos y "
        f"{variants_created} variantes creados."
    )


if __name__ == "__main__":
    seed_products()
