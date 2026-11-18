from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.models import AuditEvent, Order
from bladys.order_tokens import create_order_access_token


@pytest.fixture
def pending_order_id(postgres_session):
    order = Order(total=Decimal("0.00"))
    postgres_session.add(order)
    postgres_session.flush()
    order_id = order.id
    postgres_session.commit()
    return order_id


def cancel_order(client, order_id, headers):
    return client.patch(
        f"/orders/{order_id}/status", json={"status": "CANCELADO"}, headers=headers,
    )


@pytest.fixture
def cancellation_headers(pending_order_id):
    return {"X-Order-Access-Token": create_order_access_token(pending_order_id)}


def test_cancellation_records_one_event(
    postgres_client, postgres_engine, pending_order_id, cancellation_headers,
):
    """Contexto: el cliente cancela un pedido pendiente y repite la solicitud.
    Garantía: la cancelación persiste con un solo evento y los estados correctos.
    Escenarios: éxito inicial y reintento rechazado con HTTP 409.
    """
    response = cancel_order(postgres_client, pending_order_id, cancellation_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELADO"
    with Session(postgres_engine) as db:
        audit = db.scalars(select(AuditEvent)).one()
        audit_id = audit.id
        assert audit.event_type == "ORDER_STATUS_CHANGED"
        assert audit.order_id == pending_order_id
        assert audit.actor_type == "CUSTOMER"
        assert audit.actor_user_id is None
        assert audit.payment_id is None
        assert audit.changes == {
            "order_status": {"from": "PENDIENTE_ACEPTACION", "to": "CANCELADO"},
        }
        assert db.get(Order, pending_order_id).status == "CANCELADO"

    assert cancel_order(postgres_client, pending_order_id, cancellation_headers).status_code == 409
    with Session(postgres_engine) as db:
        assert db.scalars(select(AuditEvent.id)).all() == [audit_id]


@pytest.mark.parametrize("credential,expected_status", [
    ("missing", 401), ("invalid", 401), ("other_order", 403),
])
def test_cancellation_without_valid_token_records_no_event(
    postgres_client, postgres_engine, pending_order_id, credential, expected_status,
):
    """Contexto: alguien intenta cancelar un pedido sin acreditar su acceso.
    Garantía: el pedido sigue pendiente y la auditoría queda vacía.
    Escenarios: token ausente, inválido o emitido para otro pedido.
    """
    headers = {
        "missing": {},
        "invalid": {"X-Order-Access-Token": "invalid"},
        "other_order": {"X-Order-Access-Token": create_order_access_token(pending_order_id + 1)},
    }[credential]
    assert cancel_order(postgres_client, pending_order_id, headers).status_code == expected_status
    with Session(postgres_engine) as db:
        assert db.get(Order, pending_order_id).status == "PENDIENTE_ACEPTACION"
        assert db.scalars(select(AuditEvent)).all() == []


def test_cancellation_rolls_back_when_audit_insert_fails(
    postgres_client, postgres_engine, pending_order_id, cancellation_headers,
):
    """Contexto: PostgreSQL rechaza el evento al confirmar la cancelación.
    Garantía: el pedido vuelve a su estado pendiente y no queda ningún evento.
    Escenarios: el cambio de estado ya ocurrió dentro de la transacción cuando falla el INSERT.
    """
    def invalidate_actor(mapper, connection, audit):
        status = connection.scalar(select(Order.status).where(Order.id == pending_order_id))
        assert status == "CANCELADO"
        audit.actor_type = "INVALID"

    event.listen(AuditEvent, "before_insert", invalidate_actor)
    try:
        with pytest.raises(IntegrityError) as failure:
            cancel_order(postgres_client, pending_order_id, cancellation_headers)
    finally:
        event.remove(AuditEvent, "before_insert", invalidate_actor)

    assert failure.value.orig.diag.constraint_name == "ck_audit_events_actor_type"
    with Session(postgres_engine) as db:
        assert db.get(Order, pending_order_id).status == "PENDIENTE_ACEPTACION"
        assert db.scalars(select(AuditEvent)).all() == []


def test_concurrent_cancellations_record_one_event(
    postgres_client, postgres_engine, pending_order_id, cancellation_headers,
):
    """Contexto: dos solicitudes con acceso válido cancelan el mismo pedido.
    Garantía: solo una cancelación se confirma y genera un evento.
    Escenarios: ambas solicitan el cambio sobre el pedido pendiente; una gana y la otra recibe 409.
    """
    ready = Barrier(2, timeout=15)

    def synchronize_updates(connection, cursor, statement, parameters, context, executemany):
        if context.isupdate and context.compiled.statement.table.name == "orders":
            ready.wait()

    event.listen(postgres_engine, "before_cursor_execute", synchronize_updates)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(cancel_order, postgres_client, pending_order_id, cancellation_headers)
                for _ in range(2)
            ]
            status_codes = sorted(future.result(timeout=25).status_code for future in futures)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", synchronize_updates)

    assert status_codes == [200, 409]
    with Session(postgres_engine) as db:
        audit = db.scalars(select(AuditEvent)).one()
        assert (audit.order_id, audit.event_type) == (pending_order_id, "ORDER_STATUS_CHANGED")
        assert db.get(Order, pending_order_id).status == "CANCELADO"
