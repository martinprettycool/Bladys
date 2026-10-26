from decimal import Decimal

from bladys.models import Product, ProductVariant


def test_empty_catalog(client):
    response = client.get("/products")
    assert response.status_code == 200
    assert response.json() == []


def test_available_products_and_variants(client, session):
    session.add_all([
        Product(
            id=1, name="Pizza Italiana", description="Pizza",
            is_available=True,
            variants=[
                ProductVariant(id=10, name="Personal", price=Decimal("3.00"), is_available=True),
                ProductVariant(id=11, name="Familiar", price=Decimal("9.00"), is_available=False),
            ],
        ),
        Product(
            id=2, name="Hidden", is_available=False,
            variants=[ProductVariant(id=12, name="Personal", price=Decimal("4.00"), is_available=True)],
        ),
        Product(id=3, name="No variants", is_available=True),
        Product(
            id=4, name="Only hidden variants", is_available=True,
            variants=[ProductVariant(id=13, name="Personal", price=Decimal("5.00"), is_available=False)],
        ),
    ])
    session.commit()
    session.expunge_all()

    response = client.get("/products")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1, "name": "Pizza Italiana", "description": "Pizza",
            "variants": [{"id": 10, "name": "Personal", "price": "3.00"}],
        },
        {"id": 3, "name": "No variants", "description": None, "variants": []},
        {"id": 4, "name": "Only hidden variants", "description": None, "variants": []},
    ]
