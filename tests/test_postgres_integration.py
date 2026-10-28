import pytest
from bladys.models import Product, ProductVariant, Order
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

    # Read committed state after both request transactions have finished.
    with Session(postgres_engine) as verification_session:
        order = verification_session.scalars(select(Order)).one()
        item = verification_session.scalars(select(OrderItem)).one()
        record = verification_session.scalars(select(IdempotencyRecord)).one()
        assert order.id == order_id
        assert item.order_id == order_id
        assert item.variant_id == postgres_order_variant_id
        assert item.quantity == 2
        assert record.order_id == order_id
        assert record.key == key
        assert record.request_hash == build_request_hash({postgres_order_variant_id: 2})
