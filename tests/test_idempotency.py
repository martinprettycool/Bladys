from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hypothesis import given
from hypothesis import strategies as st 

from bladys.models import IdempotencyRecord, Order, OrderItem, Product, ProductVariant
from bladys.idempotency import build_request_hash

from bladys.idempotency import build_request_hash, canonicalize_order_intent


def test_same_intent_with_different_key_order_has_same_canonical_form_and_hash():
    first = {10: 3, 11: 1}
    second = {11: 1, 10: 3}

    assert canonicalize_order_intent(first) == canonicalize_order_intent(second)
    assert build_request_hash(first) == build_request_hash(second)


def test_different_quantities_produce_different_hashes():
    assert build_request_hash({10: 3, 11: 1}) != build_request_hash({10: 4, 11: 1})


def test_canonical_form_is_sorted_by_variant_id():
    quantities = {11: 1, 2: 5, 10: 3}

    assert canonicalize_order_intent(quantities) == [(2, 5), (10, 3), (11, 1)]


@pytest.fixture
def idempotency_client(client, session):
    session.add(Product(
        id=1, name="Pizza", is_available=True,
        variants=[ProductVariant(
            id=10, name="Personal", price=Decimal("3.00"), is_available=True,
        )],
    ))
    session.commit()
    return client


def test_new_key_creates_order_and_idempotency_record(idempotency_client, session):
    response = idempotency_client.post(
        "/orders", headers={"Idempotency-Key": "K123"},
        json={"items": [{"variant_id": 10, "quantity": 2}]},
    )

    assert response.status_code == 201
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
    order = session.scalars(select(Order)).one()
    record = session.scalars(select(IdempotencyRecord)).one()
    assert order.id == response.json()["id"]
    assert record.key == "K123"
    assert record.order_id == order.id
    assert record.request_hash == build_request_hash({10: 2})


def test_same_key_and_intent_reuses_order(idempotency_client, session):
    payload = {"items": [{"variant_id": 10, "quantity": 2}]}
    headers = {"Idempotency-Key": "K123"}
    first = idempotency_client.post("/orders", headers=headers, json=payload)
    assert first.status_code == 201

    retry = idempotency_client.post("/orders", headers=headers, json=payload)

    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
    record = session.scalars(select(IdempotencyRecord)).one()
    assert record.key == "K123"
    assert record.order_id == first.json()["id"]


def test_same_key_with_different_intent_conflicts(idempotency_client, session):
    first = idempotency_client.post(
        "/orders", headers={"Idempotency-Key": "K123"},
        json={"items": [{"variant_id": 10, "quantity": 2}]},
    )
    assert first.status_code == 201
    original_record = session.execute(select(
        IdempotencyRecord.id, IdempotencyRecord.key,
        IdempotencyRecord.request_hash, IdempotencyRecord.order_id,
        IdempotencyRecord.created_at,
    )).one()
    session.rollback()

    conflict = idempotency_client.post(
        "/orders", headers={"Idempotency-Key": "K123"},
        json={"items": [{"variant_id": 10, "quantity": 3}]},
    )

    assert conflict.status_code == 409
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
    assert session.scalars(select(Order.id)).one() == first.json()["id"]
    assert session.execute(select(
        IdempotencyRecord.id, IdempotencyRecord.key,
        IdempotencyRecord.request_hash, IdempotencyRecord.order_id,
        IdempotencyRecord.created_at,
    )).one() == original_record


def test_different_key_with_same_intent_creates_another_order(idempotency_client, session):
    payload = {"items": [{"variant_id": 10, "quantity": 2}]}
    first = idempotency_client.post(
        "/orders", headers={"Idempotency-Key": "K123"}, json=payload,
    )
    assert first.status_code == 201

    second = idempotency_client.post(
        "/orders", headers={"Idempotency-Key": "K999"}, json=payload,
    )

    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert session.scalar(select(func.count()).select_from(Order)) == 2
    assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2
    assert set(session.scalars(select(Order.id))) == {
        first.json()["id"], second.json()["id"],
    }
    records = session.scalars(select(IdempotencyRecord)).all()
    assert {record.key: record.order_id for record in records} == {
        "K123": first.json()["id"], "K999": second.json()["id"],
    }
    assert records[0].request_hash == records[1].request_hash
@given(
        quantities = st.dictionaries(
            keys=st.integers(min_value=1, max_value=1000),
            values=st.integers(min_value=1, max_value=1000),
            min_size=1
        )
)
def test_request_hash_is_independent_of_dict_order(quantities):
    reversed_quantities = dict(reversed(list(quantities.items())))
    assert build_request_hash(quantities) == build_request_hash(reversed_quantities)


@pytest.mark.parametrize("quantity, expected_status", [(2, 201), (3, 409)])
def test_integrity_error_recovers_persisted_winner(
    idempotency_client, engine, monkeypatch, quantity, expected_status,
):
    headers = {"Idempotency-Key": "race-winner"}
    winner = idempotency_client.post(
        "/orders", headers=headers,
        json={"items": [{"variant_id": 10, "quantity": 2}]},
    )
    assert winner.status_code == 201
    original_scalar = Session.scalar
    hidden_reads = []
    failed_inserts = []

    def hide_initial_record(self, statement, *args, **kwargs):
        # Only the initial lookup misses the already committed winner.
        if not hidden_reads and statement.column_descriptions[0]["entity"] is IdempotencyRecord:
            hidden_reads.append(True)
            return None
        return original_scalar(self, statement, *args, **kwargs)

    def record_integrity_error(context):
        if isinstance(context.sqlalchemy_exception, IntegrityError):
            failed_inserts.append(context.sqlalchemy_exception)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "scalar", hide_initial_record)
        event.listen(engine, "handle_error", record_integrity_error)
        try:
            response = idempotency_client.post(
                "/orders", headers=headers,
                json={"items": [{"variant_id": 10, "quantity": quantity}]},
            )
        finally:
            event.remove(engine, "handle_error", record_integrity_error)

    assert hidden_reads == [True]
    assert len(failed_inserts) == 1
    assert "UNIQUE constraint failed: idempotency_records.key" in str(failed_inserts[0].orig)
    assert response.status_code == expected_status
    if expected_status == 201:
        assert response.json() == winner.json()
    with Session(engine) as session:
        assert session.scalars(select(Order.id)).all() == [winner.json()["id"]]
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 1
        record = session.scalars(select(IdempotencyRecord)).one()
        assert record.key == headers["Idempotency-Key"]
        assert record.order_id == winner.json()["id"]
        assert record.request_hash == build_request_hash({10: 2})


def test_unrelated_integrity_error_is_reraised(idempotency_client, engine):
    failure = IntegrityError("INSERT INTO order_items", {}, ValueError("unrelated failure"))
    inserted_orders = []

    def record_order(mapper, connection, target):
        inserted_orders.append(target.id)

    def fail_item(mapper, connection, target):
        raise failure

    event.listen(Order, "after_insert", record_order)
    event.listen(OrderItem, "before_insert", fail_item)
    try:
        with pytest.raises(IntegrityError) as caught:
            idempotency_client.post(
                "/orders", headers={"Idempotency-Key": "unrelated-error"},
                json={"items": [{"variant_id": 10, "quantity": 2}]},
            )
    finally:
        event.remove(Order, "after_insert", record_order)
        event.remove(OrderItem, "before_insert", fail_item)

    assert caught.value is failure
    assert len(inserted_orders) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0
