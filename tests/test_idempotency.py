from decimal import Decimal

import pytest
from sqlalchemy import func, select

from bladys.models import IdempotencyRecord, Order, Product, ProductVariant

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
