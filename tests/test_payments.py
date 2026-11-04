from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bladys.order_tokens import create_order_access_token
from bladys.models import Order, Payment, UserRole
from test_orders import seeded_order_client


@pytest.fixture(autouse=True)
def proof_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path / "media" / "payment_proofs"


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
    return client.post(f"/orders/{order_id}/payment/proof", headers=headers, data={"expected_version": "1"},
                       files={"file": ("receipt.jpg", b"receipt bytes", "image/jpeg")})


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
    assert Path(proof.json()["proof_reference"]).read_bytes() == b"receipt bytes"
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
    assert Path(response.json()["proof_reference"]).read_bytes() == b"receipt bytes"
    assert response.json()["status"] == "PENDIENTE_VERIFICACION"
    with Session(engine) as verification:
        payment = verification.get(Payment, payment_id)
        assert payment.proof_reference == response.json()["proof_reference"]
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


@pytest.mark.parametrize("mime,extension", [("image/jpeg", ".jpg"), ("image/png", ".png")])
def test_real_upload(client, payment_order, engine, mime, extension):
    order_id, payment_id = payment_order
    content = b"uploaded image bytes\x00\xff"
    response = client.post(
        f"/orders/{order_id}/payment/proof",
        headers={"X-Order-Access-Token": create_order_access_token(order_id)},
        data={"expected_version": "1"},
        files={"file": ("untrusted.name", content, mime)},
    )
    assert response.status_code == 200
    reference = response.json()["proof_reference"]
    assert Path(reference).suffix == extension
    assert Path(reference).read_bytes() == content
    with Session(engine) as db:
        payment = db.get(Payment, payment_id)
        assert payment.status == "PENDIENTE_VERIFICACION"
        assert payment.proof_reference == reference


@pytest.mark.parametrize("content,mime,expected", [
    (b"invalid", "text/plain", 415),
    (b"", "image/png", 400),
    (b"x" * (5 * 1024 * 1024 + 1), "image/jpeg", 413),
], ids=["invalid-mime", "empty", "oversized"])
def test_invalid_upload_preserves_payment(client, payment_order, engine, proof_storage, content, mime, expected):
    order_id, payment_id = payment_order
    response = client.post(
        f"/orders/{order_id}/payment/proof",
        headers={"X-Order-Access-Token": create_order_access_token(order_id)},
        data={"expected_version": "1"},
        files={"file": ("proof", content, mime)},
    )
    assert response.status_code == expected
    assert not proof_storage.exists()
    with Session(engine) as db:
        payment = db.get(Payment, payment_id)
        assert payment.status == "PENDIENTE_COMPROBANTE"
        assert payment.proof_reference is None


@pytest.mark.parametrize("location", ["bearer", "order_header"])
def test_staff_jwt_cannot_submit_proof(client, payment_order, staff_headers, proof_storage, location):
    jwt_header = staff_headers[UserRole.RECEPTION]["Authorization"]
    headers = {"Authorization": jwt_header} if location == "bearer" else {
        "X-Order-Access-Token": jwt_header.removeprefix("Bearer ")}
    assert submit(client, payment_order[0], headers).status_code == 401
    assert not proof_storage.exists()


@pytest.mark.parametrize("failure", ["update", "commit"])
def test_failed_database_operation_cleans_new_file(client, payment_order, engine, proof_storage, failure):
    """Contexto: se guarda un archivo pero falla la transaccion del pago.
    Garantía: se revierte el pago y se elimina solo el archivo nuevo.
    Escenarios: falla el UPDATE o el commit; un comprobante anterior se conserva.
    """
    order_id, payment_id = payment_order
    proof_storage.mkdir(parents=True)
    previous = proof_storage / "previous.jpg"
    previous.write_bytes(b"previous")

    def fail_update(connection, cursor, statement, parameters, context, executemany):
        if context.isupdate and context.compiled.statement.table.name == "payments":
            assert len(list(proof_storage.iterdir())) == 2
            raise RuntimeError("database failure")

    def fail_commit(session):
        assert len(list(proof_storage.iterdir())) == 2
        raise RuntimeError("database failure")

    target, name, listener = (engine, "before_cursor_execute", fail_update) if failure == "update" else (Session, "before_commit", fail_commit)
    event.listen(target, name, listener)
    try:
        with pytest.raises(RuntimeError, match="database failure"):
            submit(client, order_id)
    finally:
        event.remove(target, name, listener)
    assert list(proof_storage.iterdir()) == [previous]
    assert previous.read_bytes() == b"previous"
    with Session(engine) as db:
        payment = db.get(Payment, payment_id)
        assert payment.status == "PENDIENTE_COMPROBANTE"
        assert payment.proof_reference is None


def test_conflict_cleans_new_file_and_preserves_existing(client, payment_order, engine, proof_storage):
    """Contexto: dos envios intentan registrar un comprobante para el mismo pago.
    Garantía: el UPDATE condicional conserva el ganador y limpia el archivo rechazado.
    Escenarios: el primer envio confirma; el segundo recibe conflicto.
    """
    first = submit(client, payment_order[0])
    assert first.status_code == 200
    reference = first.json()["proof_reference"]
    assert submit(client, payment_order[0]).status_code == 409
    assert list(proof_storage.iterdir()) == [Path(reference).resolve()]
    assert Path(reference).read_bytes() == b"receipt bytes"
    with Session(engine) as db:
        assert db.get(Payment, payment_order[1]).proof_reference == reference



@pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
def test_reception_retrieves_proof(client, payment_order, staff_headers, mime):
    order_id, payment_id = payment_order
    content = b"stored proof bytes\x00\xff"
    upload = client.post(
        f"/orders/{order_id}/payment/proof",
        headers={"X-Order-Access-Token": create_order_access_token(order_id)},
        data={"expected_version": "1"},
        files={"file": ("receipt", content, mime)},
    )
    assert upload.status_code == 200
    response = client.get(f"/payments/{payment_id}/proof", headers=staff_headers[UserRole.RECEPTION])
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == mime
    assert client.get("/" + upload.json()["proof_reference"]).status_code == 404


@pytest.mark.parametrize("role,expected", [(None, 401), (UserRole.KITCHEN, 403)])
def test_proof_retrieval_requires_reception(client, payment_order, staff_headers, role, expected):
    assert submit(client, payment_order[0]).status_code == 200
    headers = {} if role is None else staff_headers[role]
    response = client.get(f"/payments/{payment_order[1]}/proof", headers=headers)
    assert response.status_code == expected


@pytest.mark.parametrize("case", ["missing-payment", "no-reference", "missing-file", "directory", "unsupported-extension"])
def test_proof_retrieval_not_found(client, payment_order, engine, staff_headers, proof_storage, case):
    payment_id = payment_order[1]
    if case == "missing-payment":
        payment_id += 1000
    elif case not in {"no-reference"}:
        proof_storage.mkdir(parents=True)
        target = proof_storage / "missing.jpg"
        if case == "directory":
            target.mkdir()
        elif case == "unsupported-extension":
            target = proof_storage / "proof.txt"
            target.write_bytes(b"not an image")
        with Session(engine) as db:
            db.get(Payment, payment_id).proof_reference = target.as_posix()
            db.commit()
    response = client.get(f"/payments/{payment_id}/proof", headers=staff_headers[UserRole.RECEPTION])
    assert response.status_code == 404


@pytest.mark.parametrize("reference_kind", ["traversal", "absolute", "sibling-directory"])
def test_proof_reference_cannot_serve_outside_storage(client, payment_order, engine, staff_headers, tmp_path, reference_kind):
    outside = tmp_path / "media" / "payment_proofs_other" / "secret.jpg"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"private server data")
    references = {
        "traversal": "media/payment_proofs/../payment_proofs_other/secret.jpg",
        "absolute": outside.as_posix(),
        "sibling-directory": "media/payment_proofs_other/secret.jpg",
    }
    with Session(engine) as db:
        db.get(Payment, payment_order[1]).proof_reference = references[reference_kind]
        db.commit()
    response = client.get(f"/payments/{payment_order[1]}/proof", headers=staff_headers[UserRole.RECEPTION])
    assert response.status_code == 404
    assert b"private server data" not in response.content
    assert str(tmp_path) not in response.text


def test_proof_symlink_cannot_serve_outside_storage(client, payment_order, engine, staff_headers, proof_storage, tmp_path):
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"private server data")
    proof_storage.mkdir(parents=True)
    link = proof_storage / "linked.jpg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    with Session(engine) as db:
        db.get(Payment, payment_order[1]).proof_reference = link.as_posix()
        db.commit()
    response = client.get(f"/payments/{payment_order[1]}/proof", headers=staff_headers[UserRole.RECEPTION])
    assert response.status_code == 404
    assert b"private server data" not in response.content
