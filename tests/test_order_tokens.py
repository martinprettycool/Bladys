import base64
import json
from datetime import datetime, timezone

import jwt
import pytest
from sqlalchemy import func, select

from bladys.config import JWT_ALGORITHM, get_order_access_settings
from bladys.models import IdempotencyRecord, Order, Payment
from bladys.order_tokens import create_order_access_token
from test_orders import seeded_order_client


def decode_order_token(token):
    return jwt.decode(token, get_order_access_settings().secret_key, algorithms=[JWT_ALGORITHM])


def test_order_access_token_claims_and_default_lifetime(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    token = create_order_access_token(42)
    assert isinstance(token, str)
    claims = decode_order_token(token)
    assert set(claims) == {"sub", "purpose", "iat", "exp"}
    assert claims["sub"] == "42"
    assert claims["purpose"] == "order_access"
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["exp"], int)
    assert abs(datetime.now(timezone.utc).timestamp() - claims["iat"]) < 5
    assert claims["exp"] > claims["iat"]
    assert claims["exp"] - claims["iat"] == 1440 * 60


def test_order_access_lifetime_can_be_configured(monkeypatch):
    monkeypatch.setenv("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    claims = decode_order_token(create_order_access_token(42))
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_modified_order_token_fails_signature_verification():
    header, payload, signature = create_order_access_token(42).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = "99"
    modified_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(jwt.InvalidSignatureError):
        decode_order_token(f"{header}.{modified_payload}.{signature}")


def test_staff_secret_cannot_validate_order_token(monkeypatch):
    from bladys.config import get_jwt_settings

    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-staff-secret-distinct-from-order-123456789")
    token = create_order_access_token(42)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, get_jwt_settings().secret_key, algorithms=[JWT_ALGORITHM])


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_order_secret_is_required_without_staff_fallback(monkeypatch, secret):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-staff-secret-distinct-from-order-123456789")
    if secret is None:
        monkeypatch.delenv("ORDER_ACCESS_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("ORDER_ACCESS_SECRET_KEY", secret)
    with pytest.raises(RuntimeError, match="ORDER_ACCESS_SECRET_KEY must be configured"):
        create_order_access_token(42)


@pytest.mark.parametrize("lifetime", ["0", "-1", "invalid"])
def test_invalid_order_token_lifetime_is_rejected(monkeypatch, lifetime):
    monkeypatch.setenv("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES", lifetime)
    with pytest.raises(RuntimeError, match="ORDER_ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer"):
        create_order_access_token(42)


def test_order_creation_and_retry_issue_tokens_for_same_order(seeded_order_client, session, monkeypatch):
    """Contexto: un cliente reintenta crear un pedido con la misma clave.
    Garantía: ambos resultados contienen una capacidad para el mismo pedido sin duplicarlo.
    Escenarios: primera creación y reintento con nueva vigencia de emisión.
    """
    headers = {"Idempotency-Key": "order-capability"}
    payload = {"items": [{"variant_id": 10, "quantity": 2}]}
    first = seeded_order_client.post("/orders", headers=headers, json=payload)
    assert first.status_code == 201
    first_claims = decode_order_token(first.json()["order_access_token"])
    monkeypatch.setenv("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES", "10")
    retry = seeded_order_client.post("/orders", headers=headers, json=payload)
    assert retry.status_code == 201
    retry_claims = decode_order_token(retry.json()["order_access_token"])
    assert first.json()["id"] == retry.json()["id"]
    for claims in (first_claims, retry_claims):
        assert claims["sub"] == str(first.json()["id"])
        assert claims["purpose"] == "order_access"
    assert retry_claims["exp"] - retry_claims["iat"] == 10 * 60
    first_body, retry_body = first.json(), retry.json()
    first_body.pop("order_access_token")
    retry_body.pop("order_access_token")
    assert first_body == retry_body
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_token_issuance_failure_rolls_back_creation(seeded_order_client, session, monkeypatch):
    """Contexto: no se puede firmar la capacidad del nuevo pedido.
    Garantía: no queda un pedido, pago o clave de idempotencia parcialmente creado.
    Escenarios: falla la emisión después de insertar el pedido y antes del commit.
    """
    monkeypatch.delenv("ORDER_ACCESS_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ORDER_ACCESS_SECRET_KEY must be configured"):
        seeded_order_client.post("/orders", headers={"Idempotency-Key": "issuance-failure"},
                                 json={"items": [{"variant_id": 10, "quantity": 2}]})
    for model in (Order, Payment, IdempotencyRecord):
        assert session.scalar(select(func.count()).select_from(model)) == 0
