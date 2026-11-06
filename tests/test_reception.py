import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from bladys.models import Payment, Order, UserRole, Product, ProductVariant
from decimal import Decimal
from test_orders import seeded_order_client


def test_reception_order_requires_current_role(seeded_order_client, staff_headers):
    client = seeded_order_client
    order = client.post('/orders', headers={'Idempotency-Key':'reception-read'}, json={'items':[{'variant_id':10,'quantity':2}]}).json()
    path = f"/reception/orders/{order['id']}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'X-Order-Access-Token':order['order_access_token']}).status_code == 401
    assert client.get(path, headers=staff_headers[UserRole.KITCHEN]).status_code == 403
    response = client.get(path, headers=staff_headers[UserRole.RECEPTION])
    assert response.status_code == 200
    assert response.json() == {k:v for k,v in order.items() if k != 'order_access_token'}
    assert client.get('/reception/orders/99999', headers=staff_headers[UserRole.RECEPTION]).status_code == 404


@pytest.mark.parametrize('decision', ['APROBADO', 'RECHAZADO'])
def test_postgres_real_reception_flow(postgres_client, postgres_engine, postgres_staff_headers, tmp_path, monkeypatch, decision):
    """Contexto: un cliente envia una transferencia que recepcion debe revisar.
    Garantia: comprobante y decision pertenecen al pedido; solo pago aprobado habilita cocina.
    Escenarios: aprobacion y rechazo independientes, intento de cocina y decision repetida.
    """
    monkeypatch.chdir(tmp_path)
    with Session(postgres_engine) as db:
        product = Product(name='Pizza', variants=[ProductVariant(name='Personal',price=Decimal('7.25'))])
        db.add(product); db.commit(); variant_id = product.variants[0].id
    client = postgres_client
    reception = postgres_staff_headers[UserRole.RECEPTION]
    kitchen = postgres_staff_headers[UserRole.KITCHEN]
    order = client.post('/orders', headers={'Idempotency-Key':'real-reception'},json={'items':[{'variant_id':variant_id,'quantity':2}]}).json()
    guest = {'X-Order-Access-Token':order['order_access_token']}
    assert client.get('/payments/pending',headers=reception).json() == []
    from io import BytesIO
    from PIL import Image
    image = BytesIO(); Image.new('RGB',(2,2),'blue').save(image,format='PNG'); content = image.getvalue()
    upload = client.post(f"/orders/{order['id']}/payment/proof",headers=guest,data={'expected_version':order['version']},files={'file':('proof.png',content,'image/png')})
    assert upload.status_code == 200
    payment = upload.json(); path = f"/payments/{payment['id']}"
    assert client.get('/payments/pending',headers=reception).json() == [payment]
    assert client.get(f"/reception/orders/{order['id']}",headers=reception).json()['total'] == '14.50'
    assert client.get(path+'/proof').status_code == 401
    assert client.get(path+'/proof',headers=kitchen).status_code == 403
    assert client.get(path+'/proof',headers=reception).content == content
    assert client.patch(path+'/status',headers=kitchen,json={'status':decision}).status_code == 403
    assert client.patch(f"/kitchen/orders/{order['id']}/status",headers=kitchen,json={'status':'PREPARANDO'}).status_code == 409
    assert client.patch(path+'/status',headers=reception,json={'status':decision}).status_code == 200
    assert client.patch(path+'/status',headers=reception,json={'status':'RECHAZADO' if decision == 'APROBADO' else 'APROBADO'}).status_code == 409
    assert client.get('/payments/pending',headers=reception).json() == []
    assert client.get(f"/orders/{order['id']}/payment",headers=guest).json()['status'] == decision
    assert client.put(f"/orders/{order['id']}/items",headers=guest,json={'expected_version':order['version'],'items':[{'variant_id':variant_id,'quantity':1}]}).status_code == 409
    assert client.patch(f"/kitchen/orders/{order['id']}/status",headers=kitchen,json={'status':'PREPARANDO'}).status_code == (200 if decision == 'APROBADO' else 409)
    with Session(postgres_engine) as db:
        assert db.scalar(select(Payment).where(Payment.id == payment['id'])).status == decision
        assert db.get(Order,order['id']).status == ('PREPARANDO' if decision == 'APROBADO' else 'CANCELADO')


@pytest.mark.parametrize('winner', ['APROBADO', 'RECHAZADO'])
def test_opposite_decisions_serialize(postgres_client, postgres_engine, postgres_session, postgres_staff_headers, winner):
    """Contexto: dos recepcionistas deciden sobre el mismo comprobante simultaneamente.
    Garantia: solo una decision se confirma y el pedido conserva el estado correspondiente.
    Escenarios: aprobacion primero y rechazo primero, con el segundo proceso bloqueado en PostgreSQL.
    """
    from test_order_modification_audit import serialized_race
    order = Order(total=Decimal('0.00'), payment=Payment(status='PENDIENTE_VERIFICACION',proof_reference='proof.png'))
    postgres_session.add(order); postgres_session.commit()
    order_id, payment_id = order.id, order.payment.id
    postgres_session.rollback()
    headers = postgres_staff_headers[UserRole.RECEPTION]
    other = 'RECHAZADO' if winner == 'APROBADO' else 'APROBADO'
    def decide(status):
        return postgres_client.patch(f'/payments/{payment_id}/status',headers=headers,json={'status':status})
    first, second = serialized_race(postgres_engine,lambda:decide(winner),lambda:decide(other))
    assert first.status_code == 200 and second.status_code == 409
    with Session(postgres_engine) as db:
        assert db.get(Payment,payment_id).status == winner
        assert db.get(Order,order_id).status == ('CANCELADO' if winner == 'RECHAZADO' else 'PENDIENTE_ACEPTACION')
