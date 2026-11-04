from decimal import Decimal
from itertools import product

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.order_tokens import create_order_access_token
from bladys.models import Payment, Order, OrderItem, Product, ProductVariant, UserRole


STATES = (
    "PENDIENTE_ACEPTACION", "PREPARANDO", "LISTO",
    "ENVIADO", "RECHAZADO", "CANCELADO",
)
VALID_TRANSITIONS = {
    ("PENDIENTE_ACEPTACION", "PREPARANDO"),
    ("PENDIENTE_ACEPTACION", "CANCELADO"),
    ("PREPARANDO", "LISTO"),
    ("LISTO", "ENVIADO"),
}


@pytest.mark.parametrize("source,target", list(product(STATES, ("CANCELADO", "PREPARANDO", "LISTO", "ENVIADO"))))
def test_order_lifecycle_transitions(client, session, engine, source, target, staff_headers):
    variant = ProductVariant(name="Personal", price=Decimal("3.00"))
    session.add(Product(name="Pizza", variants=[variant]))
    session.flush()
    order = Order(
        status=source, total=Decimal("6.00"), payment=Payment(status="APROBADO"),
        items=[OrderItem(variant_id=variant.id, quantity=2, unit_price=Decimal("3.00"))],
    )
    session.add(order)
    session.commit()
    order_id = order.id
    session.rollback()

    prefix = "/orders" if target == "CANCELADO" else "/kitchen/orders"
    response = client.patch(f"{prefix}/{order_id}/status", json={"status": target},
                            headers=({"X-Order-Access-Token": create_order_access_token(order_id)}
                                     if target == "CANCELADO" else staff_headers[UserRole.KITCHEN]))

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
            "id": order_id, "version": 1, "status": target, "total": "6.00",
            "items": [{"variant_id": variant.id, "quantity": 2, "unit_price": "3.00", "selected_flavors": []}],
        }
    else:
        assert f"{source} -> {target}" in response.json()["detail"]


@pytest.mark.parametrize("target", ["CANCELADO", "PREPARANDO", "LISTO", "ENVIADO"])
def test_missing_order_returns_404(client, target, staff_headers):
    prefix = "/orders" if target == "CANCELADO" else "/kitchen/orders"
    response = client.patch(f"{prefix}/999/status", json={"status": target},
                            headers=({"X-Order-Access-Token": create_order_access_token(999)}
                                     if target == "CANCELADO" else staff_headers[UserRole.KITCHEN]))
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
    response = client.patch(f"/orders/{order.id}/status", json=payload, headers={
        "X-Order-Access-Token": create_order_access_token(order.id),
    })
    assert response.status_code == 422
    session.refresh(order)
    assert order.status == "PENDIENTE_ACEPTACION"


def test_kitchen_lifecycle_and_late_customer_cancellation(client, session, staff_headers):
    order = Order(total=Decimal("0.00"), payment=Payment(status="APROBADO"))
    session.add(order)
    session.commit()
    url = f"/orders/{order.id}/status"
    for target in ("PREPARANDO", "LISTO", "ENVIADO"):
        response = client.patch(f"/kitchen/orders/{order.id}/status", json={"status": target},
                                headers=staff_headers[UserRole.KITCHEN])
        assert response.status_code == 200
        assert response.json()["status"] == target
        assert client.patch(url, json={"status": "CANCELADO"}, headers={
            "X-Order-Access-Token": create_order_access_token(order.id),
        }).status_code == 409
    session.refresh(order)
    assert order.status == "ENVIADO"


def test_pending_customer_cancellation(client, session):
    order = Order(total=Decimal("0.00"))
    session.add(order)
    session.commit()
    response = client.patch(f"/orders/{order.id}/status", json={"status": "CANCELADO"}, headers={
        "X-Order-Access-Token": create_order_access_token(order.id),
    })
    assert response.status_code == 200
    session.refresh(order)
    assert order.status == "CANCELADO"


def test_database_rejects_unsupported_order_status(session):
    session.add(Order(status="ENTREGADO", total=Decimal("0.00")))
    with pytest.raises(IntegrityError, match="ck_orders_status"):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("credential", ["missing", "tampered", "other_order"])
def test_cancellation_requires_matching_order_token(client, session, engine, credential):
    order_a, order_b = Order(total=Decimal("0.00")), Order(total=Decimal("0.00"))
    session.add_all([order_a, order_b])
    session.commit()
    ids = [order_a.id, order_b.id]
    token_a = create_order_access_token(ids[0])
    headers = {}
    if credential == "tampered":
        header, _, signature = token_a.split(".")
        payload_b = create_order_access_token(ids[1]).split(".")[1]
        headers["X-Order-Access-Token"] = f"{header}.{payload_b}.{signature}"
    elif credential == "other_order":
        headers["X-Order-Access-Token"] = token_a
    session.rollback()
    response = client.patch(f"/orders/{ids[1]}/status", headers=headers,
                            json={"status": "CANCELADO"})
    assert response.status_code == (403 if credential == "other_order" else 401)
    with Session(engine) as verification:
        assert [verification.get(Order, order_id).status for order_id in ids] == [
            "PENDIENTE_ACEPTACION", "PENDIENTE_ACEPTACION",
        ]
