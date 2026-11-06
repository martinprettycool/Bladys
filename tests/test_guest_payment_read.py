import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from bladys.models import Payment
from bladys.order_tokens import create_order_access_token
from test_orders import seeded_order_client


def create(client, key):
    response = client.post('/orders', headers={'Idempotency-Key': key}, json={'items': [{'variant_id': 10, 'quantity': 1}]})
    assert response.status_code == 201
    return response.json()


def test_guest_can_only_read_own_payment(seeded_order_client):
    client = seeded_order_client
    a, b = create(client, 'a'), create(client, 'b')
    path = f"/orders/{a['id']}/payment"
    response = client.get(path, headers={'X-Order-Access-Token': a['order_access_token']})
    assert response.status_code == 200
    assert response.json() == {'id': response.json()['id'], 'order_id': a['id'], 'status': 'PENDIENTE_COMPROBANTE', 'proof_reference': None}
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'X-Order-Access-Token': 'invalid'}).status_code == 401
    assert client.get(path, headers={'X-Order-Access-Token': b['order_access_token']}).status_code == 403


@pytest.mark.parametrize('status,proof', [('PENDIENTE_VERIFICACION', 'proof.jpg'), ('APROBADO', 'proof.jpg'), ('RECHAZADO', 'proof.jpg')])
def test_guest_reads_actual_payment_state(seeded_order_client, engine, status, proof):
    order = create(seeded_order_client, 'state')
    with Session(engine) as db:
        payment = db.scalar(select(Payment).where(Payment.order_id == order['id']))
        payment.status, payment.proof_reference = status, proof
        db.commit()
    body = seeded_order_client.get(f"/orders/{order['id']}/payment", headers={'X-Order-Access-Token': order['order_access_token']}).json()
    assert body['status'] == status and body['proof_reference'] == proof


def test_missing_guest_payment_and_order_are_not_fabricated(seeded_order_client, engine):
    order = create(seeded_order_client, 'missing')
    with Session(engine) as db:
        db.delete(db.scalar(select(Payment).where(Payment.order_id == order['id'])))
        db.commit()
    assert seeded_order_client.get(f"/orders/{order['id']}/payment", headers={'X-Order-Access-Token': order['order_access_token']}).status_code == 404
    assert seeded_order_client.get('/orders/999/payment', headers={'X-Order-Access-Token': create_order_access_token(999)}).status_code == 404
