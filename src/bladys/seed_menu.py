"""Explicit convergent seed: python -m bladys.seed_menu. Never runs on startup."""
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from bladys.models import Product, ProductVariant

PIZZA_SIZES = (("Personal", "3.00"), ("Pequeña", "5.00"), ("Mediana", "10.00"), ("Familiar", "15.00"))
PIZZAS = (
    ("ESPECIAL", "Pepperoni, salame, jamón, champiñón, pimiento verde y mozzarella."),
    ("ITALIANA", "Pepperoni, salchicha italiana, pimiento verde, cebolla y mozzarella."),
    ("NAPOLITANA", "Albahaca, orégano y mozzarella."),
    ("TOCINETA", "Carne, tocino, cebolla y mozzarella."),
    ("HAWAIANA", "Jamón, piña y mozzarella."),
    ("CARNITAS", "Carne, pollo, chorizo, champiñones, pimiento y mozzarella."),
    ("CARNITAS DULCE", "Carne, chorizo, champiñones, maíz dulce y mozzarella."),
    ("VEGETARIANA", "Tomate, cebolla, champiñones, pimiento verde y mozzarella."),
)
HAMBURGERS = (
    ("ORIGINAL", "2.50", "Carne, cheddar, papas y salsa de la casa.", None),
    ("DOBLE O NADA", "3.40", "2 carnes, cheddar, papas, huevo y salsa de la casa.", None),
    ("TOCINETA", "3.25", "Carne, cheddar, tocino, papas y salsa de la casa.", None),
    ("CHORI", "2.50", "Carne, cheddar, chorizo, huevo y papas.", None),
    ("POLLO CLÁSICO", "2.00", "Pechuga, vegetales, cheddar y papas.", None),
    ("BBQ", "3.00", "Pechuga, vegetales, tocino, mozzarella, papas y salsa BBQ.", None),
)
WINGS = (
    ("INDIVIDUAL", "2.70", "4 alitas + papas", 1),
    ("DOBLE", "5.00", "8 alitas + papas", 1),
    ("GRANDE", "7.50", "12 alitas + papas", 2),
    ("FAMILIAR", "12.50", "20 alitas + papas + gaseosa de 1 litro", 2),
    ("COMELONES", "16.00", "26 alitas + papas + gaseosa de 1 litro", 3),
)
FRIES = (
    ("Papas + Salchicha", "1.50", None, None),
    ("Papas + Pollo", "2.50", None, None),
    ("Papas + Salchicha + Huevo", "1.75", None, None),
    ("Papas + Cheddar + Tocino + Huevo", "2.75", None, None),
)
# Explicit aliases from the previous development seed; unknown rows are untouched.
LEGACY_PIZZA_NAMES = {
    "ESPECIAL": "Especial", "ITALIANA": "Italiana", "NAPOLITANA": "Napolitana",
    "TOCINETA": "Tocineta", "HAWAIANA": "Hawayana", "CARNITAS": "Carnívora",
    "CARNITAS DULCE": "Carne Dulce", "VEGETARIANA": "Vegetariana",
}


def seed_menu(session: Session) -> None:
    """Converge known menu metadata in the caller's transaction, preserving availability.

    Ambiguous existing names fail rather than guessing which IDs to reuse.
    Unknown products/variants and historical items are never removed.
    """
    menu = [(name, "PIZZA", description,
             tuple((size, price, None, None) for size, price in PIZZA_SIZES))
            for name, description in PIZZAS]
    menu += [("Hamburguesas", "HAMBURGUESA", None, HAMBURGERS),
             ("Alitas", "ALITAS", None, WINGS), ("Papitas", "PAPITAS", None, FRIES)]
    for name, category, description, variants in menu:
        names = {name, LEGACY_PIZZA_NAMES.get(name, name)}
        matches = session.scalars(select(Product).where(Product.name.in_(names))).all()
        if len(matches) > 1:
            raise ValueError(f"Ambiguous menu product: {name}")
        product = matches[0] if matches else Product(name=name)
        session.add(product)
        product.name, product.category, product.description = name, category, description
        session.flush()
        for variant_name, price, variant_description, max_flavors in variants:
            matches = session.scalars(select(ProductVariant).where(
                ProductVariant.product_id == product.id, ProductVariant.name == variant_name,
            )).all()
            if len(matches) > 1:
                raise ValueError(f"Ambiguous menu variant: {name}/{variant_name}")
            variant = matches[0] if matches else ProductVariant(product_id=product.id, name=variant_name)
            variant.price = Decimal(price)
            variant.description = variant_description
            variant.max_flavors = max_flavors
            session.add(variant)
    session.flush()


def main():
    from bladys.database import SessionLocal
    with SessionLocal.begin() as session:
        seed_menu(session)
    print("Menú MVP sincronizado: 11 productos y 47 variantes del menú; filas ajenas conservadas.")


if __name__ == "__main__":
    main()
