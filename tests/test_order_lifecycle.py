from decimal import Decimal
from itertools import product

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.models import Order, OrderItem, Product, ProductVariant


STATES = (
    "PENDIENTE_ACEPTACION", "PREPARANDO", "LISTO",
    "ENVIADO", "RECHAZADO", "CANCELADO",
)
VALID_TRANSITIONS = {
    ("PENDIENTE_ACEPTACION", "PREPARANDO"),
    ("PENDIENTE_ACEPTACION", "RECHAZADO"),
    ("PENDIENTE_ACEPTACION", "CANCELADO"),
    ("PREPARANDO", "LISTO"),
    ("LISTO", "ENVIADO"),
}


@pytest.mark.parametrize("source,target", list(product(STATES, repeat=2)))
def test_order_lifecycle_transitions(client, session, engine, source, target):
    variant = ProductVariant(name="Personal", price=Decimal("3.00"))
    session.add(Product(name="Pizza", variants=[variant]))
    session.flush()
    order = Order(
        status=source, total=Decimal("6.00"),
        items=[OrderItem(variant_id=variant.id, quantity=2, unit_price=Decimal("3.00"))],
    )
    session.add(order)
    session.commit()
    order_id = order.id
    session.rollback()

    response = client.patch(f"/orders/{order_id}/status", json={"status": target})

    valid = (source, target) in VALID_TRANSITIONS
    assert response.status_code == (200 if valid else 409)
    with Session(engine) as verification:
        persisted = verification.get(Order, order_id)
        assert persisted.status == (target if valid else source)
        assert persisted.total == Decimal("6.00")
        assert persisted.items[0].quantity == 2
        assert persisted.items[0].unit_price == Decimal("3.00")
    if valid:
        assert response.json() == {
            "id": order_id, "status": target, "total": "6.00",
            "items": [{"variant_id": variant.id, "quantity": 2, "unit_price": "3.00"}],
        }
    else:
        assert f"{source} -> {target}" in response.json()["detail"]


@pytest.mark.parametrize("target", STATES)
def test_missing_order_returns_404(client, target):
    response = client.patch("/orders/999/status", json={"status": target})
    assert response.status_code == 404


@pytest.mark.parametrize("payload", [
    {}, {"status": "ENTREGADO"}, {"status": "PAGADO"},
    {"status": "CANCELADO", "items": []},
    {"status": "PREPARANDO", "total": "0.01"},
])
def test_invalid_status_payload_is_rejected(client, session, payload):
    order = Order(total=Decimal("0.00"))
    session.add(order)
    session.commit()
    response = client.patch(f"/orders/{order.id}/status", json=payload)
    assert response.status_code == 422
    session.refresh(order)
    assert order.status == "PENDIENTE_ACEPTACION"


def test_kitchen_lifecycle_and_late_customer_cancellation(client, session):
    order = Order(total=Decimal("0.00"))
    session.add(order)
    session.commit()
    url = f"/orders/{order.id}/status"
    for target in ("PREPARANDO", "LISTO", "ENVIADO"):
        response = client.patch(url, json={"status": target})
        assert response.status_code == 200
        assert response.json()["status"] == target
        assert client.patch(url, json={"status": "CANCELADO"}).status_code == 409
    session.refresh(order)
    assert order.status == "ENVIADO"


def test_pending_customer_cancellation(client, session):
    order = Order(total=Decimal("0.00"))
    session.add(order)
    session.commit()
    response = client.patch(f"/orders/{order.id}/status", json={"status": "CANCELADO"})
    assert response.status_code == 200
    session.refresh(order)
    assert order.status == "CANCELADO"


def test_database_rejects_unsupported_order_status(session):
    session.add(Order(status="ENTREGADO", total=Decimal("0.00")))
    with pytest.raises(IntegrityError, match="ck_orders_status"):
        session.commit()
    session.rollback()
