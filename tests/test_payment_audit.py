from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.models import AuditEvent, Order, Payment, User, UserRole


@pytest.fixture
def pending_payment(postgres_session, postgres_staff_headers):
    order = Order(total=Decimal("6.00"), payment=Payment(
        status="PENDIENTE_VERIFICACION", proof_reference="opaque-proof-not-for-audit",
    ))
    postgres_session.add(order)
    postgres_session.commit()
    ids = order.id, order.payment.id
    postgres_session.rollback()
    return ids


@pytest.mark.parametrize("decision,event_type", [
    ("APROBADO", "PAYMENT_APPROVED"), ("RECHAZADO", "PAYMENT_REJECTED"),
])
def test_payment_decision_records_one_event(
    postgres_client, postgres_engine, pending_payment, postgres_staff_headers, decision, event_type,
):
    """Contexto: recepcion decide un pago pendiente y luego repite la solicitud.
    Garantia: una decision exitosa guarda un evento con el empleado y los cambios reales.
    Escenarios: aprobacion o rechazo con cancelacion; el reintento 409 no agrega eventos.
    """
    order_id, payment_id = pending_payment
    headers = postgres_staff_headers[UserRole.RECEPTION]
    path = f"/payments/{payment_id}/status"
    assert postgres_client.patch(path, json={"status": decision}, headers=headers).status_code == 200
    assert postgres_client.patch(path, json={"status": decision}, headers=headers).status_code == 409
    with Session(postgres_engine) as db:
        audit = db.scalars(select(AuditEvent)).one()
        employee = db.scalar(select(User).where(User.role == UserRole.RECEPTION))
        assert (audit.order_id, audit.payment_id, audit.actor_type, audit.actor_user_id) == (
            order_id, payment_id, "STAFF", employee.id,
        )
        assert audit.event_type == event_type
        changes = {"payment_status": {"from": "PENDIENTE_VERIFICACION", "to": decision}}
        if decision == "RECHAZADO":
            changes["order_status"] = {"from": "PENDIENTE_ACEPTACION", "to": "CANCELADO"}
        assert audit.changes == changes
        assert db.get(Payment, payment_id).status == decision
        assert db.get(Order, order_id).status == (
            "CANCELADO" if decision == "RECHAZADO" else "PENDIENTE_ACEPTACION"
        )


@pytest.mark.parametrize("identity,expected", [("missing", 401), ("invalid", 401), ("kitchen", 403)])
def test_unauthorized_decision_has_no_event(
    postgres_client, postgres_engine, pending_payment, postgres_staff_headers, identity, expected,
):
    """Contexto: un visitante o empleado sin permisos intenta decidir un pago.
    Garantia: la autorizacion fallida no modifica el negocio ni produce auditoria de exito.
    Escenarios: sin token, token invalido y rol de cocina.
    """
    order_id, payment_id = pending_payment
    headers = {"missing": {}, "invalid": {"Authorization": "Bearer invalid"},
               "kitchen": postgres_staff_headers[UserRole.KITCHEN]}[identity]
    response = postgres_client.patch(f"/payments/{payment_id}/status", json={"status": "APROBADO"}, headers=headers)
    assert response.status_code == expected
    with Session(postgres_engine) as db:
        assert db.scalars(select(AuditEvent)).all() == []
        assert db.get(Payment, payment_id).status == "PENDIENTE_VERIFICACION"
        assert db.get(Order, order_id).status == "PENDIENTE_ACEPTACION"


@pytest.mark.parametrize("decision", ["APROBADO", "RECHAZADO"])
def test_audit_insert_failure_rolls_back_payment_and_order(
    postgres_client, postgres_engine, pending_payment, postgres_staff_headers, decision,
):
    """Contexto: PostgreSQL rechaza la auditoria cuando recepcion confirma una decision.
    Garantia: el mismo rollback revierte pago, cancelacion del pedido y evento.
    Escenarios: fallo real del INSERT al aprobar o rechazar, despues de actualizar el negocio.
    """
    order_id, payment_id = pending_payment
    with postgres_engine.begin() as db:
        db.execute(text("ALTER TABLE audit_events ADD CONSTRAINT test_reject_audit CHECK (event_type = 'NEVER')"))
    with pytest.raises(IntegrityError) as failure:
        postgres_client.patch(f"/payments/{payment_id}/status", json={"status": decision},
                              headers=postgres_staff_headers[UserRole.RECEPTION])
    assert failure.value.orig.diag.constraint_name == "test_reject_audit"
    with Session(postgres_engine) as db:
        assert db.get(Payment, payment_id).status == "PENDIENTE_VERIFICACION"
        assert db.get(Order, order_id).status == "PENDIENTE_ACEPTACION"
        assert db.scalars(select(AuditEvent)).all() == []


def test_concurrent_approvals_record_one_event(
    postgres_client, postgres_engine, pending_payment, postgres_staff_headers,
):
    """Contexto: dos solicitudes de recepcion aprueban simultaneamente el mismo pago.
    Garantia: una sola transicion confirma y genera exactamente un evento de aprobacion.
    Escenarios: ambas solicitudes llegan al bloqueo del pedido; una gana y la otra recibe 409.
    """
    order_id, payment_id = pending_payment
    ready = Barrier(2, timeout=15)

    def synchronize(connection, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        if compiled is not None and context.isinsert is False:
            query = compiled.statement
            if (getattr(query, "is_select", False)
                    and query._for_update_arg is not None
                    and query.get_final_froms()[0].name == "orders"):
                ready.wait()

    def approve():
        return postgres_client.patch(f"/payments/{payment_id}/status", json={"status": "APROBADO"},
                                     headers=postgres_staff_headers[UserRole.RECEPTION])

    event.listen(postgres_engine, "before_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve) for _ in range(2)]
            responses = [future.result(timeout=25) for future in futures]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", synchronize)
    assert sorted(response.status_code for response in responses) == [200, 409]
    with Session(postgres_engine) as db:
        audit = db.scalars(select(AuditEvent)).one()
        assert audit.event_type == "PAYMENT_APPROVED"
        assert (audit.order_id, audit.payment_id) == (order_id, payment_id)
        assert db.get(Payment, payment_id).status == "APROBADO"


@pytest.mark.parametrize("decision", ["APROBADO", "RECHAZADO"])
def test_sqlite_compatibility_keeps_audit_enabled(client, session, staff_headers, decision):
    """Contexto: las pruebas rapidas SQLite tambien ejecutan decisiones de recepcion.
    Garantia: la adaptacion de tipos conserva la escritura real de auditoria.
    Escenarios: aprobacion y rechazo guardan un evento, sin reemplazar las pruebas PostgreSQL.
    """
    order = Order(total=Decimal("6.00"), payment=Payment(status="PENDIENTE_VERIFICACION"))
    session.add(order)
    session.commit()
    payment_id = order.payment.id
    session.rollback()
    response = client.patch(f"/payments/{payment_id}/status", json={"status": decision},
                            headers=staff_headers[UserRole.RECEPTION])
    assert response.status_code == 200
    audit = session.scalars(select(AuditEvent)).one()
    assert audit.payment_id == payment_id
    assert audit.changes["payment_status"] == {"from": "PENDIENTE_VERIFICACION", "to": decision}
