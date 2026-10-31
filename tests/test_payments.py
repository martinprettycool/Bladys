import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.order_tokens import create_order_access_token
from bladys.models import Order, Payment, UserRole
from test_orders import seeded_order_client


@pytest.fixture
def payment_order(seeded_order_client, engine):
    response = seeded_order_client.post(
        "/orders", headers={"Idempotency-Key": "payment"},
        json={"items": [{"variant_id": 10, "quantity": 1}]},
    )
    assert response.status_code == 201
    order_id = response.json()["id"]
    with Session(engine) as db:
        payment = db.scalars(select(Payment)).one()
        assert payment.order_id == order_id
        assert payment.status == "PENDIENTE_COMPROBANTE"
        assert payment.proof_reference is None
        return order_id, payment.id


def submit(client, order_id, headers=None):
    if headers is None:
        headers = {"X-Order-Access-Token": create_order_access_token(order_id)}
    return client.post(f"/orders/{order_id}/payment/proof", headers=headers,
                       json={"proof_reference": "opaque:receipt-123"})


def decide(client, payment_id, status, staff_headers):
    return client.patch(f"/payments/{payment_id}/status", json={"status": status}, headers=staff_headers[UserRole.RECEPTION])


def test_creation_retry_has_one_payment(client, payment_order, engine):
    order_id, payment_id = payment_order
    response = client.post("/orders", headers={"Idempotency-Key": "payment"},
                           json={"items": [{"variant_id": 10, "quantity": 1}]})
    assert response.json()["id"] == order_id
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.get(Payment, payment_id).order_id == order_id


@pytest.mark.parametrize("decision", ["APROBADO", "RECHAZADO"])
def test_proof_and_reception(client, payment_order, engine, decision, staff_headers):
    order_id, payment_id = payment_order
    assert client.get("/payments/pending", headers=staff_headers[UserRole.RECEPTION]).json() == []
    proof = submit(client, order_id)
    assert proof.status_code == 200
    assert proof.json()["status"] == "PENDIENTE_VERIFICACION"
    assert proof.json()["proof_reference"] == "opaque:receipt-123"
    assert [p["id"] for p in client.get("/payments/pending", headers=staff_headers[UserRole.RECEPTION]).json()] == [payment_id]
    assert decide(client, payment_id, decision, staff_headers).status_code == 200
    assert client.get("/payments/pending", headers=staff_headers[UserRole.RECEPTION]).json() == []
    assert submit(client, order_id).status_code == 409
    assert decide(client, payment_id, "APROBADO", staff_headers).status_code == 409
    with Session(engine) as db:
        assert db.get(Payment, payment_id).status == decision
        assert db.get(Order, order_id).status == (
            "CANCELADO" if decision == "RECHAZADO" else "PENDIENTE_ACEPTACION"
        )


@pytest.mark.parametrize("status", ["PENDIENTE_VERIFICACION", "APROBADO", "RECHAZADO"])
def test_proof_cannot_be_resubmitted(client, payment_order, engine, status):
    order_id, payment_id = payment_order
    with Session(engine) as db:
        payment = db.get(Payment, payment_id)
        payment.status = status
        payment.proof_reference = "original"
        db.commit()
    assert submit(client, order_id).status_code == 409
    with Session(engine) as db:
        assert db.get(Payment, payment_id).proof_reference == "original"
        assert db.get(Payment, payment_id).status == status


@pytest.mark.parametrize("status", ["PENDIENTE_COMPROBANTE", "PENDIENTE_VERIFICACION", "RECHAZADO", "APROBADO"])
def test_kitchen_gate_and_visibility(client, payment_order, engine, status, staff_headers):
    order_id, payment_id = payment_order
    with Session(engine) as db:
        db.get(Payment, payment_id).status = status
        db.commit()
    assert [o["id"] for o in client.get("/kitchen/orders/pending", headers=staff_headers[UserRole.KITCHEN]).json()] == (
        [order_id] if status == "APROBADO" else []
    )
    response = client.patch(f"/kitchen/orders/{order_id}/status", json={"status": "PREPARANDO"}, headers=staff_headers[UserRole.KITCHEN])
    assert response.status_code == (200 if status == "APROBADO" else 409)
    with Session(engine) as db:
        assert db.get(Order, order_id).status == (
            "PREPARANDO" if status == "APROBADO" else "PENDIENTE_ACEPTACION"
        )
    assert client.get("/kitchen/orders/pending", headers=staff_headers[UserRole.KITCHEN]).json() == []


def test_paid_customer_cancellation(client, payment_order, engine, staff_headers):
    order_id, payment_id = payment_order
    assert submit(client, order_id).status_code == 200
    assert decide(client, payment_id, "APROBADO", staff_headers).status_code == 200
    assert client.patch(f"/orders/{order_id}/status", json={"status": "CANCELADO"},
                        headers={"X-Order-Access-Token": create_order_access_token(order_id)}).status_code == 200
    with Session(engine) as db:
        assert db.get(Order, order_id).status == "CANCELADO"
        assert db.get(Payment, payment_id).status == "APROBADO"
    assert client.get("/kitchen/orders/pending", headers=staff_headers[UserRole.KITCHEN]).json() == []


def test_rejection_rolls_back_if_cancellation_fails(client, payment_order, engine, staff_headers):
    """Contexto: falla la cancelaciÃ³n al rechazar un pago.
    GarantÃ­a: pago y pedido conservan su estado previo si falla la operaciÃ³n.
    Escenarios: el pago se actualiza pero la cancelaciÃ³n falla antes del commit.
    """
    order_id, payment_id = payment_order
    assert submit(client, order_id).status_code == 200

    def fail_cancel(connection, cursor, statement, parameters, context, executemany):
        if context.isupdate and context.compiled.statement.table.name == "orders":
            raise RuntimeError("cancellation failure")

    event.listen(engine, "before_cursor_execute", fail_cancel)
    try:
        with pytest.raises(RuntimeError, match="cancellation failure"):
            decide(client, payment_id, "RECHAZADO", staff_headers)
    finally:
        event.remove(engine, "before_cursor_execute", fail_cancel)
    with Session(engine) as db:
        assert db.get(Payment, payment_id).status == "PENDIENTE_VERIFICACION"
        assert db.get(Order, order_id).status == "PENDIENTE_ACEPTACION"


def test_payment_insert_failure_rolls_back_order(client, seeded_order_client, engine):
    def fail(mapper, connection, target):
        raise RuntimeError("payment insert failure")
    event.listen(Payment, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="payment insert failure"):
            client.post("/orders", headers={"Idempotency-Key": "failed-payment"},
                        json={"items": [{"variant_id": 10, "quantity": 1}]})
    finally:
        event.remove(Payment, "before_insert", fail)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(Payment)) == 0


@pytest.mark.parametrize("decision", ["APROBADO", "RECHAZADO"])
def test_decision_requires_verification(client, payment_order, decision, staff_headers):
    assert decide(client, payment_order[1], decision, staff_headers).status_code == 409


def test_missing_payment_and_invalid_payload(client, staff_headers, payment_order):
    assert submit(client, 999).status_code == 404
    assert decide(client, 999, "APROBADO", staff_headers).status_code == 404
    assert decide(client, 999, "PENDIENTE_COMPROBANTE", staff_headers).status_code == 422
    assert client.post(f"/orders/{payment_order[0]}/payment/proof",
                       headers={"X-Order-Access-Token": create_order_access_token(payment_order[0])},
                       json={"proof_reference": ""}).status_code == 422


def test_payment_unique_order_and_valid_state(payment_order, engine):
    with Session(engine) as db:
        db.add(Payment(order_id=payment_order[0]))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.get(Payment, payment_order[1]).status = "UNKNOWN"
        with pytest.raises(IntegrityError, match="ck_payments_status"):
            db.commit()


def test_owned_proof_submission_persists_reference_and_transition(client, payment_order, engine):
    order_id, payment_id = payment_order
    response = submit(client, order_id)
    assert response.status_code == 200
    assert response.json()["proof_reference"] == "opaque:receipt-123"
    assert response.json()["status"] == "PENDIENTE_VERIFICACION"
    with Session(engine) as verification:
        payment = verification.get(Payment, payment_id)
        assert payment.proof_reference == "opaque:receipt-123"
        assert payment.status == "PENDIENTE_VERIFICACION"


@pytest.mark.parametrize("credential", ["missing", "tampered", "other_order"])
def test_proof_requires_matching_order_token(client, payment_order, engine, credential):
    order_a_id, _ = payment_order
    other = client.post(
        "/orders", headers={"Idempotency-Key": "other-proof-order"},
        json={"items": [{"variant_id": 10, "quantity": 1}]},
    )
    assert other.status_code == 201
    order_b_id = other.json()["id"]
    token_a = create_order_access_token(order_a_id)
    headers = {}
    if credential == "tampered":
        header, _, signature = token_a.split(".")
        payload_b = other.json()["order_access_token"].split(".")[1]
        headers["X-Order-Access-Token"] = f"{header}.{payload_b}.{signature}"
    elif credential == "other_order":
        headers["X-Order-Access-Token"] = token_a
    response = submit(client, order_b_id, headers=headers)
    assert response.status_code == (403 if credential == "other_order" else 401)
    with Session(engine) as verification:
        payments = verification.scalars(select(Payment).where(
            Payment.order_id.in_([order_a_id, order_b_id]),
        )).all()
        assert len(payments) == 2
        for payment in payments:
            assert payment.status == "PENDIENTE_COMPROBANTE"
            assert payment.proof_reference is None
