"""Regression coverage for payment-safe, versioned customer replacements."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from bladys.models import Order, OrderItem, Payment, Product, ProductVariant


@pytest.fixture
def audit_order(postgres_client, postgres_engine):
    with Session(postgres_engine) as db:
        variant = ProductVariant(name="Audit", price=Decimal("3.00"))
        db.add(Product(name="Audit", variants=[variant]))
        db.commit()
        variant_id = variant.id
    response = postgres_client.post("/orders", headers={"Idempotency-Key": "audit-create"},
        json={"items": [{"variant_id": variant_id, "quantity": 2}]})
    assert response.status_code == 201
    body = response.json()
    return body["id"], variant_id, {"X-Order-Access-Token": body["order_access_token"]}


def replace(client, order, quantity, expected_version=1):
    order_id, variant_id, headers = order
    return client.put(f"/orders/{order_id}/items", headers=headers,
        json={"expected_version": expected_version, "items": [{"variant_id": variant_id, "quantity": quantity}]})


def test_unpaid_edit_preserves_order_payment_and_authoritative_total(postgres_client, postgres_engine, audit_order):
    """Contexto: un invitado cambia cantidades antes de transferir.
    Garantia: conserva pedido y pago, y usa el precio actual del catalogo.
    Escenarios: edicion valida de un pedido pendiente sin comprobante.
    """
    order_id, variant_id, _ = audit_order
    with Session(postgres_engine) as db:
        payment_id = db.scalar(select(Payment.id).where(Payment.order_id == order_id))
        db.get(ProductVariant, variant_id).price = Decimal("4.25")
        db.commit()
    response = replace(postgres_client, audit_order, 3)
    assert response.status_code == 200
    assert response.json()["id"] == order_id
    assert response.json()["total"] == "12.75"
    assert response.json()["version"] == 2
    with Session(postgres_engine) as db:
        payment = db.scalar(select(Payment).where(Payment.order_id == order_id))
        assert payment.id == payment_id and payment.status == "PENDIENTE_COMPROBANTE"
        assert payment.proof_reference is None
        assert db.get(Order, order_id).total == Decimal("12.75")


@pytest.mark.parametrize("status,proof", [
    ("PENDIENTE_VERIFICACION", "submitted-proof"), ("APROBADO", "approved-proof"),
    ("PENDIENTE_COMPROBANTE", "existing-proof"),
])
def test_payment_or_proof_forbids_edit(postgres_client, postgres_engine, audit_order, status, proof):
    """Contexto: ya existe comprobante o el pago ha avanzado.
    Garantia: el total que respalda el pago no puede cambiar por edicion.
    Escenarios: pago en verificacion, aprobado o referencia no nula aun pendiente.
    """
    with Session(postgres_engine) as db:
        payment = db.scalar(select(Payment).where(Payment.order_id == audit_order[0]))
        payment.status, payment.proof_reference = status, proof
        db.commit()
    response = replace(postgres_client, audit_order, 5)
    assert response.status_code == 409, f"Unsafe edit accepted: {status}, proof={proof}"
    assert response.json()["detail"]["code"] == "PAYMENT_NOT_EDITABLE"
    with Session(postgres_engine) as db:
        order = db.get(Order, audit_order[0])
        assert order.version == 1 and order.total == Decimal("6.00")
        assert order.payment.status == status and order.payment.proof_reference == proof


def test_failed_insert_rolls_back_in_postgres(postgres_client, postgres_engine, audit_order):
    """Contexto: falla la insercion despues de borrar los items anteriores.
    Garantia: items, total y pago sobreviven completos al rollback.
    Escenarios: fallo durante reemplazo seguido de lectura independiente.
    """
    def fail_insert(mapper, connection, target):
        raise RuntimeError("audit insert failure")
    event.listen(OrderItem, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="audit insert failure"):
            replace(postgres_client, audit_order, 5)
    finally:
        event.remove(OrderItem, "before_insert", fail_insert)
    with Session(postgres_engine) as db:
        order = db.get(Order, audit_order[0])
        assert order.total == Decimal("6.00")
        assert order.version == 1
        assert [(i.variant_id, i.quantity) for i in order.items] == [(audit_order[1], 2)]
        assert order.payment.status == "PENDIENTE_COMPROBANTE"


def serialized_race(engine, first_operation, second_operation):
    """Force real Order lock contention; do not rely on thread scheduling."""
    locked, contender_started, release = Event(), Event(), Event()
    pids = {}
    def is_order_lock(context):
        stmt = context.compiled.statement if context.compiled else None
        return (stmt is not None and getattr(stmt, "_for_update_arg", None) is not None
                and stmt.column_descriptions[0]["entity"] is Order)
    def before(connection, cursor, statement, parameters, context, executemany):
        if is_order_lock(context):
            pid = connection.connection.driver_connection.info.backend_pid
            if not pids:
                pids["first"] = pid
            elif pid != pids["first"]:
                pids["second"] = pid
                contender_started.set()
    def after(connection, cursor, statement, parameters, context, executemany):
        if is_order_lock(context) and connection.connection.driver_connection.info.backend_pid == pids["first"]:
            locked.set()
            assert release.wait(15)
    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                first = executor.submit(first_operation)
                assert locked.wait(10)
                second = executor.submit(second_operation)
                assert contender_started.wait(10)
                deadline = monotonic() + 10
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while pids["first"] not in observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["second"]}):
                        assert monotonic() < deadline, "Second transaction did not block on Order"
                        sleep(.01)
            finally:
                release.set()
            return first.result(timeout=15), second.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)


@pytest.mark.parametrize("winner", ["edit", "proof"])
def test_edit_vs_proof_upload(postgres_client, postgres_engine, audit_order, tmp_path, monkeypatch, winner):
    """Contexto: edicion y comprobante usan la misma version del pedido.
    Garantia: nunca se guarda un comprobante para un total que cambia despues.
    Escenarios: edicion primero rechaza comprobante obsoleto; comprobante primero bloquea edicion.
    """
    monkeypatch.chdir(tmp_path)
    def edit():
        return replace(postgres_client, audit_order, 5)
    def upload():
        # Separate ASGI client: a held synchronous SQL call inside the async
        # upload must not block dispatch of the competing request in the test.
        from fastapi.testclient import TestClient
        with TestClient(postgres_client.app) as upload_client:
            return upload_client.post(f"/orders/{audit_order[0]}/payment/proof", headers=audit_order[2],
                data={"expected_version": "1"}, files={"file": ("proof.jpg", b"test proof", "image/jpeg")})
    first, second = serialized_race(postgres_engine, edit if winner == "edit" else upload,
                                    upload if winner == "edit" else edit)
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == ("ORDER_VERSION_CONFLICT" if winner == "edit" else "PAYMENT_NOT_EDITABLE")
    with Session(postgres_engine) as db:
        order = db.get(Order, audit_order[0])
        assert order.total == Decimal("15.00" if winner == "edit" else "6.00")
        assert order.version == (2 if winner == "edit" else 1)
        assert order.payment.status == ("PENDIENTE_COMPROBANTE" if winner == "edit" else "PENDIENTE_VERIFICACION")
        assert (order.payment.proof_reference is None) == (winner == "edit")
    assert len(list((tmp_path / "media" / "payment_proofs").glob("*"))) == (0 if winner == "edit" else 1)


def test_two_stale_edits_detect_conflict(postgres_client, postgres_engine, audit_order):
    """Contexto: dos clientes editan el mismo estado anterior del pedido.
    Garantia: una edicion obsoleta no sobrescribe silenciosamente la otra.
    Escenarios: solicitudes concurrentes con cantidades distintas y respuestas finales.
    """
    both_read = Barrier(2, timeout=10)
    def stale_edit(quantity):
        before = postgres_client.get(f"/orders/{audit_order[0]}", headers=audit_order[2])
        assert before.status_code == 200 and before.json()["items"][0]["quantity"] == 2
        both_read.wait()
        return replace(postgres_client, audit_order, quantity, before.json()["version"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(stale_edit, [3, 5]))
    assert sorted(r.status_code for r in responses) == [200, 409], "Both stale edits succeeded; last writer silently won"
    winner = next(response.json() for response in responses if response.status_code == 200)
    loser = next(response.json() for response in responses if response.status_code == 409)
    assert loser["detail"]["code"] == "ORDER_VERSION_CONFLICT"
    assert winner["version"] == 2
    current = postgres_client.get(f"/orders/{audit_order[0]}", headers=audit_order[2]).json()
    assert current == winner


def test_lost_response_retry_cannot_overwrite_later_edit(postgres_client, audit_order):
    """Contexto: se reintenta una edicion cuya respuesta se perdio despues de otra edicion.
    Garantia: el reintento antiguo no revierte la seleccion mas reciente.
    Escenarios: edicion A, edicion B y repeticion tardia de A.
    """
    assert replace(postgres_client, audit_order, 3).status_code == 200
    assert replace(postgres_client, audit_order, 5, expected_version=2).status_code == 200
    stale = replace(postgres_client, audit_order, 3, expected_version=1)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "ORDER_VERSION_CONFLICT"
    current = postgres_client.get(f"/orders/{audit_order[0]}", headers=audit_order[2]).json()
    assert current["version"] == 3 and current["items"][0]["quantity"] == 5


@pytest.mark.parametrize("decision", ["APROBADO", "RECHAZADO"])
@pytest.mark.parametrize("winner", ["edit", "decision"])
def test_decision_vs_edit_lock_order(postgres_client, postgres_engine, audit_order, postgres_staff_headers, decision, winner):
    """Contexto: recepcion decide un pago mientras llega una edicion tardia.
    Garantia: ambas operaciones toman Order antes de Payment, sin inversion de bloqueos.
    Escenarios: aprobacion o rechazo; cualquiera adquiere primero el bloqueo del pedido.
    """
    with Session(postgres_engine) as db:
        payment = db.scalar(select(Payment).where(Payment.order_id == audit_order[0]))
        payment.status, payment.proof_reference = "PENDIENTE_VERIFICACION", "proof"
        payment_id = payment.id
        db.commit()
    def edit():
        return replace(postgres_client, audit_order, 5)
    def decide():
        return postgres_client.patch(f"/payments/{payment_id}/status", json={"status": decision},
            headers=postgres_staff_headers["RECEPTION"])
    first, second = serialized_race(postgres_engine, edit if winner == "edit" else decide,
                                    decide if winner == "edit" else edit)
    edited, decided = (first, second) if winner == "edit" else (second, first)
    assert edited.status_code == 409 and decided.status_code == 200
    with Session(postgres_engine) as db:
        order = db.get(Order, audit_order[0])
        assert order.version == 1 and order.total == Decimal("6.00")
        assert order.payment.status == decision
        assert order.status == ("CANCELADO" if decision == "RECHAZADO" else "PENDIENTE_ACEPTACION")


def test_missing_payment_fails_closed(postgres_client, postgres_engine, audit_order):
    """Contexto: un pedido pendiente carece de su pago asociado.
    Garantia: la edicion falla cerrada sin cambiar total, items ni version.
    Escenarios: ausencia de Payment confirmada antes de solicitar el reemplazo.
    """
    with Session(postgres_engine) as db:
        payment = db.scalar(select(Payment).where(Payment.order_id == audit_order[0]))
        db.delete(payment)
        db.commit()
    response = replace(postgres_client, audit_order, 5)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "PAYMENT_MISSING"
    current = postgres_client.get(f"/orders/{audit_order[0]}", headers=audit_order[2]).json()
    assert current["version"] == 1 and current["total"] == "6.00"


@pytest.mark.parametrize("version", [None, 0, -1, True, "1", 1.5])
def test_edit_requires_strict_positive_version(postgres_client, audit_order, version):
    payload = {"items": [{"variant_id": audit_order[1], "quantity": 5}]}
    if version is not None:
        payload["expected_version"] = version
    response = postgres_client.put(f"/orders/{audit_order[0]}/items", headers=audit_order[2], json=payload)
    assert response.status_code == 422
    current = postgres_client.get(f"/orders/{audit_order[0]}", headers=audit_order[2]).json()
    assert current["version"] == 1 and current["total"] == "6.00"


@pytest.mark.parametrize("expected", [None, "0", "bad"])
def test_proof_requires_positive_version(postgres_client, audit_order, tmp_path, monkeypatch, expected):
    monkeypatch.chdir(tmp_path)
    data = {} if expected is None else {"expected_version": expected}
    response = postgres_client.post(f"/orders/{audit_order[0]}/payment/proof", headers=audit_order[2],
        data=data, files={"file": ("proof.jpg", b"proof", "image/jpeg")})
    assert response.status_code == 422
    assert not (tmp_path / "media").exists()


def test_stale_proof_cleans_file_and_preserves_payment(postgres_client, postgres_engine, audit_order, tmp_path, monkeypatch):
    """Contexto: el cliente sube un comprobante usando el total anterior a una edicion.
    Garantia: la version obsoleta no registra el pago y su archivo se elimina.
    Escenarios: version 1 cargada, edicion a version 2 y subida tardia con version 1.
    """
    monkeypatch.chdir(tmp_path)
    assert replace(postgres_client, audit_order, 5).status_code == 200
    response = postgres_client.post(f"/orders/{audit_order[0]}/payment/proof", headers=audit_order[2],
        data={"expected_version": "1"}, files={"file": ("proof.jpg", b"proof", "image/jpeg")})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ORDER_VERSION_CONFLICT"
    assert response.json()["detail"]["current_version"] == 2
    assert not list((tmp_path / "media" / "payment_proofs").glob("*"))
    with Session(postgres_engine) as db:
        order = db.get(Order, audit_order[0])
        assert order.version == 2 and order.total == Decimal("15.00")
        assert order.payment.status == "PENDIENTE_COMPROBANTE" and order.payment.proof_reference is None


def test_order_read_has_coherent_version_total_and_items(postgres_client, postgres_engine, audit_order):
    """Contexto: un cliente lee mientras otro confirma una edicion.
    Garantia: la respuesta no mezcla version y total antiguos con items nuevos.
    Escenarios: lectura iniciada antes de la edicion y completada despues del commit.
    """
    paused, release = Event(), Event()
    def hold_read(connection, cursor, statement, parameters, context, executemany):
        if "LEFT OUTER JOIN order_items" in statement and not paused.is_set():
            paused.set()
            assert release.wait(15)
    event.listen(postgres_engine, "after_cursor_execute", hold_read)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                reading = executor.submit(postgres_client.get, f"/orders/{audit_order[0]}", headers=audit_order[2])
                assert paused.wait(10)
                edited = replace(postgres_client, audit_order, 5)
                assert edited.status_code == 200
            finally:
                release.set()
            before = reading.result(timeout=10).json()
    finally:
        event.remove(postgres_engine, "after_cursor_execute", hold_read)
    assert (before["version"], before["total"], before["items"][0]["quantity"]) == (1, "6.00", 2)
    assert (edited.json()["version"], edited.json()["total"], edited.json()["items"][0]["quantity"]) == (2, "15.00", 5)



def test_each_successful_replacement_increments_version_once(postgres_client, audit_order):
    """Contexto: el cliente confirma dos reemplazos validos, incluso con items iguales.
    Garantia: cada reemplazo confirmado consume una version; un reintento antiguo no la consume.
    Escenarios: version 1 a 2, version 2 a 3 y repeticion obsoleta rechazada.
    """
    first = replace(postgres_client, audit_order, 2, expected_version=1)
    assert first.status_code == 200 and first.json()["version"] == 2
    second = replace(postgres_client, audit_order, 2, expected_version=2)
    assert second.status_code == 200 and second.json()["version"] == 3
    assert replace(postgres_client, audit_order, 2, expected_version=2).status_code == 409
    current = postgres_client.get(f"/orders/{audit_order[0]}", headers=audit_order[2]).json()
    assert current == second.json()
