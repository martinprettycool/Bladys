from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from bladys.models import AuditEvent, User, Order, OrderItem, Payment, Product, ProductVariant, UserRole


def test_active_queue_permissions(client, staff_headers):
    assert client.get('/kitchen/orders').status_code == 401
    assert client.get('/kitchen/orders', headers=staff_headers[UserRole.RECEPTION]).status_code == 403
    assert client.get('/kitchen/orders', headers=staff_headers[UserRole.KITCHEN]).json() == []


@pytest.mark.parametrize('storage', ['sqlite', 'postgres'])
def test_kitchen_operational_flow(request, storage):
    """Contexto: cocina recupera pedidos y procesa alitas con pago aprobado.
    Garantia: pago, contenido y estados autorizados se conservan en la base.
    Escenarios: pago pendiente bloqueado; aprobacion, preparacion, listo y envio;
    transicion repetida rechazada y cola recuperable entre pasos.
    """
    session = request.getfixturevalue('postgres_session' if storage == 'postgres' else 'session')
    client = request.getfixturevalue('postgres_client' if storage == 'postgres' else 'client')
    headers = request.getfixturevalue('postgres_staff_headers' if storage == 'postgres' else 'staff_headers')
    kitchen = headers[UserRole.KITCHEN]
    variant = ProductVariant(name='12 unidades', price=Decimal('12.00'), max_flavors=2)
    session.add(Product(name='Alitas', category='ALITAS', variants=[variant]))
    session.commit()
    response = client.post('/orders', headers={'Idempotency-Key':'kitchen-flow'}, json={'items':[
        {'variant_id':variant.id,'quantity':2,'selected_flavors':['BBQ','Buffalo']} ]})
    assert response.status_code == 201
    order = response.json()
    path = f"/kitchen/orders/{order['id']}/status"
    assert client.get('/kitchen/orders', headers=kitchen).json() == []
    assert client.patch(path, headers=kitchen, json={'status':'PREPARANDO'}).status_code == 409
    payment = session.scalar(select(Payment).where(Payment.order_id == order['id']))
    payment.status = 'PENDIENTE_VERIFICACION'
    session.commit()
    decision = f'/payments/{payment.id}/status'
    assert client.patch(decision, headers=kitchen, json={'status':'APROBADO'}).status_code == 403
    assert client.patch(decision, headers=kitchen, json={'status':'RECHAZADO'}).status_code == 403
    assert client.patch(decision, headers=headers[UserRole.RECEPTION], json={'status':'APROBADO'}).status_code == 200
    for target in ['PREPARANDO','LISTO','ENVIADO']:
        queue = client.get('/kitchen/orders', headers=kitchen).json()
        assert len(queue) == 1
        assert queue[0]['items'] == order['items']
        assert client.patch(path, json={'status':target}).status_code == 401
        assert client.patch(path, headers=headers[UserRole.RECEPTION], json={'status':target}).status_code == 403
        result = client.patch(path, headers=kitchen, json={'status':target})
        assert result.status_code == 200
        assert result.json()['status'] == target
        assert client.patch(path, headers=kitchen, json={'status':target}).status_code == 409
    assert client.get('/kitchen/orders', headers=kitchen).json() == []
    session.expire_all()
    assert session.get(Order, order['id']).status == 'ENVIADO'
    employee = session.scalar(select(User).where(User.role == UserRole.KITCHEN))
    audits = session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "ORDER_STATUS_CHANGED").order_by(AuditEvent.id)
    ).all()
    assert len(audits) == 3
    for audit, source, target in zip(audits, ["PENDIENTE_ACEPTACION", "PREPARANDO", "LISTO"],
                                     ["PREPARANDO", "LISTO", "ENVIADO"], strict=True):
        assert (audit.order_id, audit.actor_type, audit.actor_user_id, audit.payment_id) == (
            order["id"], "STAFF", employee.id, None,
        )
        assert audit.changes == {"order_status": {"from": source, "to": target}}


@pytest.mark.parametrize('source,target', [('PENDIENTE_ACEPTACION','PREPARANDO'),('PREPARANDO','LISTO'),('LISTO','ENVIADO')])
def test_concurrent_kitchen_transition(postgres_session, postgres_client, postgres_staff_headers, source, target):
    """Contexto: dos empleados actuan simultaneamente sobre el mismo pedido.
    Garantia: cada estado anterior solo puede consumirse una vez.
    Escenarios: ambas solicitudes compiten; una confirma y la otra recibe 409.
    """
    order = Order(status=source, total=Decimal('0.00'), payment=Payment(status='APROBADO'))
    postgres_session.add(order)
    postgres_session.commit()
    path = f'/kitchen/orders/{order.id}/status'
    postgres_session.rollback()
    barrier = Barrier(2)
    def advance():
        barrier.wait(timeout=10)
        return postgres_client.patch(path, headers=postgres_staff_headers[UserRole.KITCHEN], json={'status':target}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: advance(), range(2)))
    assert sorted(results) == [200,409]
    postgres_session.expire_all()
    assert postgres_session.get(Order, order.id).status == target


    audit = postgres_session.scalars(select(AuditEvent)).one()
    assert (audit.order_id, audit.event_type) == (order.id, "ORDER_STATUS_CHANGED")
    assert audit.changes == {"order_status": {"from": source, "to": target}}


def test_kitchen_audit_failure_rolls_back_transition(postgres_session, postgres_client, postgres_staff_headers):
    """Contexto: cocina acepta un pedido pagado y PostgreSQL rechaza su evento.
    Garantía: la transición y el evento se revierten juntos.
    Escenarios: el pedido ya está en preparación dentro de la transacción cuando falla el INSERT.
    """
    order = Order(total=Decimal("0.00"), payment=Payment(status="APROBADO"))
    postgres_session.add(order)
    postgres_session.flush()
    order_id = order.id
    postgres_session.commit()

    def invalidate_actor(mapper, connection, audit):
        assert connection.scalar(select(Order.status).where(Order.id == order_id)) == "PREPARANDO"
        audit.actor_user_id = None

    event.listen(AuditEvent, "before_insert", invalidate_actor)
    try:
        with pytest.raises(IntegrityError) as failure:
            postgres_client.patch(f"/kitchen/orders/{order_id}/status",
                                  headers=postgres_staff_headers[UserRole.KITCHEN],
                                  json={"status": "PREPARANDO"})
    finally:
        event.remove(AuditEvent, "before_insert", invalidate_actor)

    assert failure.value.orig.diag.constraint_name == "ck_audit_events_actor_user"
    assert postgres_session.get(Order, order_id).status == "PENDIENTE_ACEPTACION"
    assert postgres_session.scalars(select(AuditEvent)).all() == []
