import jwt
import pytest
from bladys.config import JWT_ALGORITHM, get_order_access_settings
from bladys.order_tokens import create_order_access_token
from bladys.models import Payment, Product, ProductVariant, Order, UserRole
from decimal import Decimal 

@pytest.fixture
def postgres_order_variant_id(postgres_session):
    product = Product(
        name="Pizza",
        is_available=True,
    )

    variant = ProductVariant(
        name="Personal",
        price=Decimal("3.00"),
        is_available=True,
    )

    product.variants = [variant]

    postgres_session.add(product)
    postgres_session.commit()

    return variant.id

def test_create_order_in_postgres(
    postgres_client,
    postgres_session,
    postgres_order_variant_id,
    clean_postgres_db,
):
    response = postgres_client.post(
        "/orders",
        headers={"Idempotency-Key": "postgres-basic"},
        json={
            "items": [
                {
                    "variant_id": postgres_order_variant_id,
                    "quantity": 2,
                }
            ]
        },
    )

    assert response.status_code == 201

    body = response.json()

    order = postgres_session.get(Order, body["id"])

    assert order is not None
    assert order.total == Decimal("6.00")

def test_concurrent_same_key_and_intent_returns_same_order(
    postgres_client,
    postgres_engine,
    postgres_session,
    postgres_order_variant_id,
    monkeypatch,
):
    """Contexto: dos clientes reintentan simultÃ¡neamente el mismo pedido.
    GarantÃ­a: existe un solo pedido y ambas respuestas autorizan ese pedido.
    Escenarios: ambas consultas iniciales fallan; una inserciÃ³n gana y la otra recupera.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    from sqlalchemy import event, func, select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from bladys.idempotency import build_request_hash
    from bladys.models import IdempotencyRecord, OrderItem

    assert postgres_engine.dialect.name == "postgresql"
    for model in (Order, OrderItem, IdempotencyRecord):
        assert postgres_session.scalar(select(func.count()).select_from(model)) == 0
    # Release the setup/read transaction before starting the requests.
    postgres_session.rollback()

    key = "postgres-concurrent-same-intent"
    payload = {"items": [{"variant_id": postgres_order_variant_id, "quantity": 2}]}
    both_lookups_missed = Barrier(2, timeout=10)
    observations_lock = Lock()
    initial_lookups = []
    unique_errors = []
    original_scalar = Session.scalar

    def synchronize_initial_lookup(session, statement, *args, **kwargs):
        result = original_scalar(session, statement, *args, **kwargs)
        if (
            session.get_bind() is postgres_engine
            and statement.column_descriptions[0]["entity"] is IdempotencyRecord
            and not session.info.get("concurrency_lookup_observed")
        ):
            session.info["concurrency_lookup_observed"] = True
            assert result is None, "Both initial lookups must miss the key"
            connection = session.connection().connection.driver_connection
            with observations_lock:
                initial_lookups.append((session, connection.info.backend_pid))
            # Neither request can create anything until BOTH real SELECTs miss.
            # Recovery reads bypass this barrier via the per-session flag above.
            both_lookups_missed.wait()
        return result

    def observe_unique_violation(context):
        if isinstance(context.sqlalchemy_exception, IntegrityError):
            with observations_lock:
                unique_errors.append((context.original_exception, context.statement))

    def post_order():
        return postgres_client.post(
            "/orders", headers={"Idempotency-Key": key}, json=payload,
        )

    with monkeypatch.context() as patch:
        patch.setattr(Session, "scalar", synchronize_initial_lookup)
        event.listen(postgres_engine, "handle_error", observe_unique_violation)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(post_order) for _ in range(2)]
                responses = [future.result(timeout=30) for future in futures]
        finally:
            event.remove(postgres_engine, "handle_error", observe_unique_violation)

    assert len(initial_lookups) == 2
    assert initial_lookups[0][0] is not initial_lookups[1][0]
    assert initial_lookups[0][1] != initial_lookups[1][1]
    assert len(unique_errors) == 1
    error, statement = unique_errors[0]
    assert error.sqlstate == "23505"  # PostgreSQL unique_violation
    assert "INSERT INTO idempotency_records" in statement
    assert [response.status_code for response in responses] == [201, 201]
    order_id = responses[0].json()["id"]
    assert responses[1].json()["id"] == order_id
    for response in responses:
        claims = jwt.decode(response.json()["order_access_token"],
                            get_order_access_settings().secret_key, algorithms=[JWT_ALGORITHM])
        assert claims["sub"] == str(order_id)
        assert claims["purpose"] == "order_access"

    # Read committed state after both request transactions have finished.
    with Session(postgres_engine) as verification_session:
        order = verification_session.scalars(select(Order)).one()
        item = verification_session.scalars(select(OrderItem)).one()
        record = verification_session.scalars(select(IdempotencyRecord)).one()
        assert verification_session.scalars(select(Payment)).one().order_id == order_id
        assert order.id == order_id
        assert item.order_id == order_id
        assert item.variant_id == postgres_order_variant_id
        assert item.quantity == 2
        assert record.order_id == order_id
        assert record.key == key
        assert record.request_hash == build_request_hash({postgres_order_variant_id: 2})


def test_concurrent_cancellation_and_preparation_only_one_wins(
    postgres_client, postgres_engine, postgres_session, postgres_staff_headers,
):
    """Contexto: cliente cancela mientras cocina acepta un pedido pagado.
    GarantÃ­a: solo una transiciÃ³n consume el estado pendiente.
    Escenarios: ambas operaciones observan pendiente y solo una confirma.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    from sqlalchemy import event, select
    from sqlalchemy.orm import Session

    assert postgres_engine.dialect.name == "postgresql"
    order = Order(total=Decimal("0.00"), payment=Payment(status="APROBADO"))
    postgres_session.add(order)
    postgres_session.commit()
    order_id = order.id
    postgres_session.rollback()

    both_pending = Barrier(2, timeout=10)
    lock = Lock()
    observations = []
    rowcounts = []

    def before_update(connection, cursor, statement, parameters, context, executemany):
        if not context.isupdate or context.compiled.statement.table.name != "orders":
            return
        # Both independent PostgreSQL transactions actually observe pending
        # before either UPDATE is sent. The SELECT is test instrumentation only.
        status = connection.scalar(select(Order.status).where(Order.id == order_id))
        with lock:
            observations.append((connection.connection.driver_connection.info.backend_pid, status))
        both_pending.wait()

    def after_update(connection, cursor, statement, parameters, context, executemany):
        if context.isupdate and context.compiled.statement.table.name == "orders":
            with lock:
                rowcounts.append(cursor.rowcount)

    def transition(target):
        return postgres_client.patch(
            f"{'/orders' if target == 'CANCELADO' else '/kitchen/orders'}/{order_id}/status",
            json={"status": target},
            headers=({"X-Order-Access-Token": create_order_access_token(order_id)}
                     if target == "CANCELADO" else postgres_staff_headers[UserRole.KITCHEN]),
        )

    event.listen(postgres_engine, "before_cursor_execute", before_update)
    event.listen(postgres_engine, "after_cursor_execute", after_update)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            targets = ("CANCELADO", "PREPARANDO")
            futures = [executor.submit(transition, target) for target in targets]
            responses = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_update)
        event.remove(postgres_engine, "after_cursor_execute", after_update)

    assert len(observations) == 2
    assert observations[0][0] != observations[1][0]
    assert [status for _, status in observations] == ["PENDIENTE_ACEPTACION"] * 2
    assert sorted(rowcounts) == [0, 1]
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(target for target, response in zip(targets, responses) if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 409)
    assert winner in loser.json()["detail"]
    with Session(postgres_engine) as verification:
        persisted = verification.get(Order, order_id)
        assert persisted.status in {"CANCELADO", "PREPARANDO"}
        assert persisted.status == winner
    for target, response in zip(targets, responses):
        if response.status_code == 200:
            assert response.json()["status"] == target


def test_postgres_rejects_unsupported_order_status(postgres_session):
    from sqlalchemy.exc import IntegrityError

    postgres_session.add(Order(status="ENTREGADO", total=Decimal("0.00")))
    with pytest.raises(IntegrityError) as caught:
        postgres_session.commit()
    assert caught.value.orig.diag.constraint_name == "ck_orders_status"
    postgres_session.rollback()


@pytest.mark.parametrize("winner", ["modification", "kitchen"])
def test_replacement_vs_preparation_row_lock(
    postgres_client, postgres_engine, postgres_session, postgres_order_variant_id, winner, postgres_staff_headers,
):
    """Contexto: cliente intenta editar un pedido pagado mientras cocina lo acepta.
    Garantia: la edicion se rechaza antes o despues de cocina sin cambiar total ni items.
    Escenarios: cada operacion adquiere primero el bloqueo y la otra espera en PostgreSQL.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from sqlalchemy import event, text
    from sqlalchemy.orm import Session

    assert postgres_engine.dialect.name == "postgresql"
    created = postgres_client.post(
        "/orders", headers={"Idempotency-Key": "replacement-race"},
        json={"items": [{"variant_id": postgres_order_variant_id, "quantity": 2}]},
    )
    assert created.status_code == 201
    order_id = created.json()["id"]
    postgres_session.query(Payment).filter_by(order_id=order_id).update({"status": "APROBADO"})
    postgres_session.commit()
    postgres_session.rollback()
    acquired = Event()
    release = Event()
    contender_started = Event()
    pids = {}

    def operation(context):
        statement = context.compiled.statement if context.compiled else None
        if context.isupdate and statement.table.name == "orders":
            # Only PATCH writes status; PUT's later total UPDATE is irrelevant.
            if "status" in context.compiled_parameters[0]:
                return "kitchen"
        if statement is not None and getattr(statement, "is_select", False):
            if getattr(statement, "_for_update_arg", None) is not None and statement.column_descriptions[0]["entity"] is Order:
                return "modification"
        return None

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        role = operation(context)
        if role:
            pids[role] = connection.connection.driver_connection.info.backend_pid
            if role != winner:
                contender_started.set()

    def after_execute(connection, cursor, statement, parameters, context, executemany):
        if operation(context) == winner:
            # The real lock/UPDATE has completed, but its transaction cannot commit yet.
            acquired.set()
            assert release.wait(15), "Timed out releasing winning transaction"

    def modify():
        return postgres_client.put(f"/orders/{order_id}/items", headers={
            "X-Order-Access-Token": created.json()["order_access_token"],
        }, json={"expected_version": created.json()["version"], "items": [
            {"variant_id": postgres_order_variant_id, "quantity": 5}
        ]})

    def prepare():
        return postgres_client.patch(f"/kitchen/orders/{order_id}/status", json={"status": "PREPARANDO"},
                                     headers=postgres_staff_headers[UserRole.KITCHEN])

    event.listen(postgres_engine, "before_cursor_execute", before_execute)
    event.listen(postgres_engine, "after_cursor_execute", after_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                first = executor.submit(modify if winner == "modification" else prepare)
                assert acquired.wait(10)
                second = executor.submit(prepare if winner == "modification" else modify)
                assert contender_started.wait(10)
                loser = "kitchen" if winner == "modification" else "modification"
                assert pids[winner] != pids[loser]
                # Prove real PostgreSQL blocking, not just overlapping Python threads.
                deadline = monotonic() + 10
                with postgres_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while True:
                        blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pids[loser]})
                        if pids[winner] in blockers:
                            break
                        assert monotonic() < deadline, "Competing request did not block on winner"
                        sleep(0.01)
            finally:
                release.set()
            first_response = first.result(timeout=20)
            second_response = second.result(timeout=20)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_execute)
        event.remove(postgres_engine, "after_cursor_execute", after_execute)

    modification_response = first_response if winner == "modification" else second_response
    kitchen_response = second_response if winner == "modification" else first_response
    assert modification_response.status_code == 409  # Approved payments prohibit editing.
    assert modification_response.json()["detail"]["code"] in {"PAYMENT_NOT_EDITABLE", "ORDER_NOT_EDITABLE"}
    assert kitchen_response.status_code == 200
    with Session(postgres_engine) as verification:
        persisted = verification.get(Order, order_id)
        assert persisted.status == "PREPARANDO"
        quantity = 2
        assert persisted.total == Decimal("3.00") * quantity
        assert [(i.variant_id, i.quantity, i.unit_price) for i in persisted.items] == [
            (postgres_order_variant_id, quantity, Decimal("3.00"))
        ]



@pytest.mark.parametrize("approval_commits_first", [False, True])
def test_approval_vs_preparation_committed_visibility(
    postgres_client, postgres_engine, postgres_session, approval_commits_first, postgres_staff_headers,
):
    """Contexto: recepciÃ³n aprueba mientras cocina intenta preparar.
    GarantÃ­a: solo una aprobaciÃ³n confirmada habilita la preparaciÃ³n.
    Escenarios: la aprobaciÃ³n confirma antes o despuÃ©s del intento de cocina.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    assert postgres_engine.dialect.name == "postgresql"
    order = Order(total=Decimal("0.00"), payment=Payment(
        status="PENDIENTE_VERIFICACION", proof_reference="opaque:proof",
    ))
    postgres_session.add(order)
    postgres_session.commit()
    order_id, payment_id = order.id, order.payment.id
    postgres_session.rollback()
    approval_updated = Event()
    release_approval = Event()
    pids = {}

    def hold_approval(connection, cursor, statement, parameters, context, executemany):
        if context.isupdate:
            table = context.compiled.statement.table.name
            pids[table] = connection.connection.driver_connection.info.backend_pid
            if table == "payments":
                # Real UPDATE completed; APROBADO is still uncommitted.
                approval_updated.set()
                assert release_approval.wait(15)

    def prepare():
        return postgres_client.patch(f"/kitchen/orders/{order_id}/status", json={"status": "PREPARANDO"},
                                     headers=postgres_staff_headers[UserRole.KITCHEN])

    event.listen(postgres_engine, "after_cursor_execute", hold_approval)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                approval = executor.submit(postgres_client.patch,
                    f"/payments/{payment_id}/status", json={"status": "APROBADO"},
                    headers=postgres_staff_headers[UserRole.RECEPTION])
                assert approval_updated.wait(10)
                if approval_commits_first:
                    release_approval.set()
                    assert approval.result(timeout=10).status_code == 200
                preparation = executor.submit(prepare).result(timeout=10)
                assert preparation.status_code == (200 if approval_commits_first else 409)
                if not approval_commits_first:
                    assert pids["payments"] != pids["orders"]
                # Independent connection sees committed state while approval is held.
                with Session(postgres_engine) as verification:
                    assert verification.get(Order, order_id).status == (
                        "PREPARANDO" if approval_commits_first else "PENDIENTE_ACEPTACION"
                    )
                    assert verification.get(Payment, payment_id).status == (
                        "APROBADO" if approval_commits_first else "PENDIENTE_VERIFICACION"
                    )
            finally:
                release_approval.set()
            assert approval.result(timeout=10).status_code == 200
    finally:
        event.remove(postgres_engine, "after_cursor_execute", hold_approval)
    if not approval_commits_first:
        assert prepare().status_code == 200
    with Session(postgres_engine) as verification:
        assert verification.get(Order, order_id).status == "PREPARANDO"
        assert verification.get(Payment, payment_id).status == "APROBADO"


@pytest.mark.parametrize("legacy_status", ["PENDIENTE_ACEPTACION", "CANCELADO", "PREPARANDO"])
def test_payment_migration_backfill_and_legacy_guard(postgres_engine, legacy_status):
    import importlib.util
    from pathlib import Path
    from uuid import uuid4
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    spec = importlib.util.spec_from_file_location(
        "payment_migration", Path(__file__).parents[1] / "alembic/versions/0005_payments.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # Isolate migration checks; roll back the entire temporary schema afterwards.
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = "payment_migration_" + uuid4().hex
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
            Order.__table__.create(connection)
            connection.execute(Order.__table__.insert().values(total=0, status=legacy_status))
            with Operations.context(MigrationContext.configure(connection)):
                if legacy_status == "PREPARANDO":
                    with pytest.raises(DBAPIError, match="Resolve legacy advanced orders"):
                        with connection.begin_nested():
                            migration.upgrade()
                    assert connection.scalar(text("SELECT to_regclass('payments')")) is None
                else:
                    migration.upgrade()
                    row = connection.execute(text("SELECT order_id, status FROM payments")).one()
                    assert row == (1, "PENDIENTE_COMPROBANTE")
        finally:
            transaction.rollback()
