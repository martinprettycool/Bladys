from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from bladys.models import Order, OrderItem, ProductVariant
from bladys.order_tokens import create_order_access_token
from test_orders import seeded_order_client


@pytest.fixture
def pending_order(seeded_order_client):
    response = seeded_order_client.post(
        "/orders", headers={"Idempotency-Key": "replacement"},
        json={"items": [{"variant_id": 10, "quantity": 2}]},
    )
    assert response.status_code == 201
    seeded_order_client.headers["X-Order-Access-Token"] = response.json()["order_access_token"]
    return response.json()["id"]


def assert_persisted(engine, order_id, items, total="6.00", status="PENDIENTE_ACEPTACION"):
    with Session(engine) as verification:
        order = verification.get(Order, order_id)
        assert order.status == status
        assert order.total == Decimal(total)
        assert sorted((i.variant_id, i.quantity, i.unit_price) for i in order.items) == items
        assert verification.scalars(select(OrderItem).where(OrderItem.order_id == order_id)).all() == order.items


@pytest.mark.parametrize("desired,expected,total", [
    ([(11, 1)], [(11, 1, Decimal("7.25"))], "7.25"),
    ([(10, 2), (11, 1)], [(10, 2, Decimal("3.00")), (11, 1, Decimal("7.25"))], "13.25"),
    ([(10, 5)], [(10, 5, Decimal("3.00"))], "15.00"),
    ([(10, 1), (10, 3)], [(10, 4, Decimal("3.00"))], "12.00"),
])
def test_replace_complete_contents(client, pending_order, engine, desired, expected, total):
    response = client.put(f"/orders/{pending_order}/items", json={"items": [
        {"variant_id": variant, "quantity": quantity} for variant, quantity in desired
    ]})
    assert response.status_code == 200
    assert response.json()["total"] == total
    assert_persisted(engine, pending_order, expected, total)
    # A second replacement removes every other variant.
    assert client.put(f"/orders/{pending_order}/items", json={"items": [
        {"variant_id": 10, "quantity": 1}
    ]}).status_code == 200
    assert_persisted(engine, pending_order, [(10, 1, Decimal("3.00"))], "3.00")


@pytest.mark.parametrize("payload", [
    {}, {"items": []},
    *({"items": [{"variant_id": 10, "quantity": q}]} for q in (0, -1, 1.5, True, "2", None)),
    {"items": [{"variant_id": 10}]},
    {"items": [{"variant_id": 10, "quantity": 1, "unit_price": "0.01"}]},
    {"items": [{"variant_id": 10, "quantity": 1}], "total": "0.01"},
])
def test_invalid_replacement(client, pending_order, engine, payload):
    assert client.put(f"/orders/{pending_order}/items", json=payload).status_code == 422
    assert_persisted(engine, pending_order, [(10, 2, Decimal("3.00"))])


@pytest.mark.parametrize("variant,status", [(999, 404), (12, 400), (20, 400)])
def test_invalid_variant_preserves_order(client, pending_order, engine, variant, status):
    response = client.put(f"/orders/{pending_order}/items", json={"items": [
        {"variant_id": 11, "quantity": 3}, {"variant_id": variant, "quantity": 1}
    ]})
    assert response.status_code == status
    assert_persisted(engine, pending_order, [(10, 2, Decimal("3.00"))])


def test_missing_order(client):
    assert client.put("/orders/999/items", headers={
        "X-Order-Access-Token": create_order_access_token(999),
    }, json={"items": [
        {"variant_id": 10, "quantity": 1}
    ]}).status_code == 404


@pytest.mark.parametrize("status", ["PREPARANDO", "LISTO", "ENVIADO", "RECHAZADO", "CANCELADO"])
def test_nonpending_replacement_rejected(client, pending_order, engine, status):
    with Session(engine) as setup:
        setup.get(Order, pending_order).status = status
        setup.commit()
    assert client.put(f"/orders/{pending_order}/items", json={"items": [
        {"variant_id": 11, "quantity": 1}
    ]}).status_code == 409
    assert_persisted(engine, pending_order, [(10, 2, Decimal("3.00"))], status=status)


def test_replacement_snapshots_current_price(client, pending_order, engine):
    with Session(engine) as setup:
        setup.get(ProductVariant, 10).price = Decimal("4.25")
        setup.commit()
    response = client.put(f"/orders/{pending_order}/items", json={"items": [
        {"variant_id": 10, "quantity": 3}
    ]})
    assert response.status_code == 200
    assert response.json()["items"][0]["unit_price"] == "4.25"
    with Session(engine) as setup:
        setup.get(ProductVariant, 10).price = Decimal("9.00")
        setup.commit()
    assert_persisted(engine, pending_order, [(10, 3, Decimal("4.25"))], "12.75")


def test_insert_failure_rolls_back_replacement(client, pending_order, engine):
    """Contexto: falla la escritura de los nuevos items.
    Garantía: el pedido conserva sus items y total anteriores.
    Escenarios: el borrado comienza pero la inserción falla y se revierte todo.
    """
    def fail_insert(mapper, connection, target):
        raise RuntimeError("replacement insert failure")

    event.listen(OrderItem, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="replacement insert failure"):
            client.put(f"/orders/{pending_order}/items", json={"items": [
                {"variant_id": 11, "quantity": 3}
            ]})
    finally:
        event.remove(OrderItem, "before_insert", fail_insert)
    assert_persisted(engine, pending_order, [(10, 2, Decimal("3.00"))])


@pytest.mark.parametrize("credential", ["missing", "tampered", "other_order"])
def test_replacement_requires_matching_order_token(client, pending_order, engine, credential):
    other = client.post(
        "/orders", headers={"Idempotency-Key": "other-replacement"},
        json={"items": [{"variant_id": 10, "quantity": 2}]},
    )
    assert other.status_code == 201
    other_id = other.json()["id"]
    token = client.headers.pop("X-Order-Access-Token")
    target_id = pending_order
    headers = {}
    if credential == "tampered":
        header, _, signature = token.split(".")
        payload = other.json()["order_access_token"].split(".")[1]
        headers["X-Order-Access-Token"] = f"{header}.{payload}.{signature}"
        target_id = other_id
    elif credential == "other_order":
        headers["X-Order-Access-Token"] = token
        target_id = other_id
    response = client.put(f"/orders/{target_id}/items", headers=headers, json={
        "items": [{"variant_id": 11, "quantity": 5}],
    })
    assert response.status_code == (403 if credential == "other_order" else 401)
    assert_persisted(engine, pending_order, [(10, 2, Decimal("3.00"))])
    assert_persisted(engine, other_id, [(10, 2, Decimal("3.00"))])
