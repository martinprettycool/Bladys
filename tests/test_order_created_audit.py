from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.models import AuditEvent, IdempotencyRecord, Order, OrderItem, Payment, Product, ProductVariant
from test_orders import seeded_order_client


@pytest.fixture
def order_variant(postgres_session):
    product = Product(name="Pizza", variants=[ProductVariant(name="Personal", price=Decimal("3.00"))])
    postgres_session.add(product)
    postgres_session.commit()
    variant_id = product.variants[0].id
    postgres_session.rollback()
    return variant_id


def test_creation_retry_and_conflict_keep_one_event(postgres_client, postgres_engine, order_variant):
    """Contexto: un cliente crea un pedido, reintenta y luego reutiliza la clave con otro contenido.
    Garantia: solo la creacion nueva confirma pedido, pago, idempotencia y evento CUSTOMER.
    Escenarios: creacion 201, reintento identico 201 y conflicto 409 sin duplicados.
    """
    headers = {"Idempotency-Key": "order-created-audit"}
    payload = {"items": [{"variant_id": order_variant, "quantity": 1}]}
    first = postgres_client.post("/orders", headers=headers, json=payload)
    assert first.status_code == 201
    with Session(postgres_engine) as db:
        audit_id = db.scalars(select(AuditEvent.id)).one()
    retry = postgres_client.post("/orders", headers=headers, json=payload)
    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    conflict = postgres_client.post("/orders", headers=headers,
                                   json={"items": [{"variant_id": order_variant, "quantity": 2}]})
    assert conflict.status_code == 409
    with Session(postgres_engine) as db:
        order = db.scalars(select(Order)).one()
        assert order.id == first.json()["id"]
        assert db.scalars(select(Payment)).one().order_id == order.id
        assert db.scalars(select(IdempotencyRecord)).one().order_id == order.id
        audit = db.scalars(select(AuditEvent)).one()
        assert audit.id == audit_id
        assert (audit.order_id, audit.payment_id, audit.actor_type, audit.actor_user_id) == (
            order.id, None, "CUSTOMER", None,
        )
        assert audit.event_type == "ORDER_CREATED"
        assert audit.changes == {"order_status": {"from": None, "to": order.status}}
        assert order.status == first.json()["status"]


def test_audit_insert_failure_rolls_back_creation(postgres_client, postgres_engine, order_variant):
    """Contexto: la base rechaza el evento al confirmar un pedido nuevo.
    Garantia: no persisten pedido, items, pago, idempotencia ni auditoria de una creacion fallida.
    Escenarios: fallo real del INSERT de auditoria y reintento exitoso con la misma clave.
    """
    with postgres_engine.begin() as db:
        db.execute(text("ALTER TABLE audit_events ADD CONSTRAINT test_reject_creation CHECK (event_type <> 'ORDER_CREATED')"))
    headers = {"Idempotency-Key": "failed-audit-creation"}
    payload = {"items": [{"variant_id": order_variant, "quantity": 1}]}
    with pytest.raises(IntegrityError) as failure:
        postgres_client.post("/orders", headers=headers, json=payload)
    assert failure.value.orig.diag.constraint_name == "test_reject_creation"
    with Session(postgres_engine) as db:
        for model in (Order, OrderItem, Payment, IdempotencyRecord, AuditEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    with postgres_engine.begin() as db:
        db.execute(text("ALTER TABLE audit_events DROP CONSTRAINT test_reject_creation"))
    assert postgres_client.post("/orders", headers=headers, json=payload).status_code == 201
    with Session(postgres_engine) as db:
        for model in (Order, OrderItem, Payment, IdempotencyRecord, AuditEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 1


def test_sqlite_creation_keeps_real_audit(seeded_order_client, engine):
    """Contexto: la suite SQLite tambien crea pedidos y procesa reintentos.
    Garantia: la compatibilidad de tipos conserva la auditoria activa.
    Escenarios: dos solicitudes identicas producen un solo evento persistido.
    """
    headers = {"Idempotency-Key": "sqlite-created-audit"}
    payload = {"items": [{"variant_id": 10, "quantity": 1}]}
    for _ in range(2):
        response = seeded_order_client.post("/orders", headers=headers, json=payload)
        assert response.status_code == 201
    with Session(engine) as db:
        audit = db.scalars(select(AuditEvent)).one()
        assert audit.event_type == "ORDER_CREATED"
        assert audit.order_id == response.json()["id"]
        assert audit.changes == {"order_status": {"from": None, "to": response.json()["status"]}}
