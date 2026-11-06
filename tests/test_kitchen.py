from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import select

from bladys.models import Order, OrderItem, Payment, Product, ProductVariant, UserRole


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
