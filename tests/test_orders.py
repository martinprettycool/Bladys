from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from bladys.models import Order, OrderItem, Product, ProductVariant


@pytest.fixture
def order_client(client, session):
    session.add(Product(
        id=1, name="Pizza", is_available=True,
        variants=[
            ProductVariant(id=10, name="Personal", price=Decimal("3.00"), is_available=True),
            ProductVariant(id=11, name="Grande", price=Decimal("7.25"), is_available=True),
            ProductVariant(id=12, name="Oculta", price=Decimal("1.00"), is_available=False),
        ],
    ))
    session.add(Product(
        id=2, name="Oculto", is_available=False,
        variants=[ProductVariant(id=20, name="Personal", price=Decimal("2.00"), is_available=True)],
    ))
    session.commit()
    return client


def assert_no_orders(engine):
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 0


def test_create_order_consolidates_and_snapshots_prices(order_client, engine):
    client = order_client
    response = client.post("/orders", headers={"Idempotency-Key": "consolidates-prices"}, json={"items": [
        {"variant_id": 10, "quantity": 2},
        {"variant_id": 11, "quantity": 1},
        {"variant_id": 10, "quantity": 3},
    ]})
    assert response.status_code == 201
    body = response.json()
    assert body == {
        "id": body["id"], "status": "PENDIENTE_ACEPTACION", "total": "22.25",
        "items": [
            {"variant_id": 10, "quantity": 5, "unit_price": "3.00"},
            {"variant_id": 11, "quantity": 1, "unit_price": "7.25"},
        ],
    }
    with Session(engine) as session:
        order = session.get(Order, body["id"])
        assert order.total == Decimal("22.25")
        assert order.status == "PENDIENTE_ACEPTACION"
        assert len(order.items) == 2
        session.get(ProductVariant, 10).price = Decimal("4.00")
        session.commit()
    with Session(engine) as session:
        item = session.scalar(select(OrderItem).where(OrderItem.variant_id == 10))
        assert item.quantity == 5
        assert item.unit_price == Decimal("3.00")


@pytest.mark.parametrize("variant_id, status", [(999, 404), (12, 400), (20, 400)])
def test_invalid_variant_leaves_no_order(order_client, engine, variant_id, status):
    client = order_client
    response = client.post("/orders", headers={"Idempotency-Key": "invalid-variant"}, json={"items": [
        {"variant_id": 10, "quantity": 1},
        {"variant_id": variant_id, "quantity": 1},
    ]})
    assert response.status_code == status
    assert_no_orders(engine)


@pytest.mark.parametrize("payload", [
    {}, {"items": []},
    {"items": [{"variant_id": 10, "quantity": 0}]},
    {"items": [{"variant_id": 10, "quantity": -1}]},
    {"items": [{"variant_id": 10, "quantity": 1.5}]},
    {"items": [{"variant_id": 10, "quantity": True}]},
    {"items": [{"variant_id": 10}]},
    {"items": [{"variant_id": 10, "quantity": 1, "unit_price": "0.01"}]},
    {"items": [{"variant_id": 10, "quantity": 1, "is_available": True}]},
    {"items": [{"variant_id": 10, "quantity": 1}], "total": "0.01"},
    {"items": [{"variant_id": 10, "quantity": 1}], "status": "PAGADO"},
    {"items": [{"variant_id": 10, "quantity": 1}], "is_available": True},
])
def test_invalid_request_is_rejected(order_client, engine, payload):
    client = order_client
    assert client.post("/orders", headers={"Idempotency-Key": "invalid-request"}, json=payload).status_code == 422
    assert_no_orders(engine)


def test_item_insert_failure_rolls_back_entire_order(order_client, engine):
    client = order_client
    inserted_orders = []

    def record_order(mapper, connection, target):
        inserted_orders.append(target.id)

    def fail_item(mapper, connection, target):
        raise RuntimeError("simulated item insert failure")

    event.listen(Order, "after_insert", record_order)
    event.listen(OrderItem, "before_insert", fail_item)
    try:
        with pytest.raises(RuntimeError, match="simulated item insert failure"):
            client.post("/orders", headers={"Idempotency-Key": "item-insert-failure"}, json={"items": [{"variant_id": 10, "quantity": 2}]})
    finally:
        event.remove(Order, "after_insert", record_order)
        event.remove(OrderItem, "before_insert", fail_item)

    assert len(inserted_orders) == 1
    assert_no_orders(engine)

def test_order_uses_current_database_price(client, session):
    # PREPARE
    product = Product(
        id=1,
        name="Pizza",
        is_available=True,
        variants=[
            ProductVariant(
                id=10,
                name="Personal",
                price=Decimal("3.00"),
                is_available=True,
            )
        ],
    )
    session.add(product)
    session.commit()
    variant = session.get(ProductVariant, 10)
    variant.price = Decimal("4.00")
    session.commit()
    # ACT
    response = client.post("/orders", headers={"Idempotency-Key": "current-database-price"}, json = {"items": [{"variant_id": 10, "quantity" : 2 }]})
    assert response.status_code == 201
    # ASSERT
    item = session.scalar(select(OrderItem).where(OrderItem.variant_id == 10))
    assert item.unit_price == Decimal("4.00")
    body = response.json()
    order = session.get(Order, body['id'])
    assert order.total == Decimal("8.00")
    
