from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from fastapi.testclient import TestClient

from bladys.config import JWT_ALGORITHM, get_jwt_settings, get_order_access_settings
from bladys.main import app
from bladys.models import Order, User, UserRole
from bladys.order_tokens import create_order_access_token, decode_order_access_token
from bladys.tokens import create_access_token, decode_access_token


@pytest.mark.parametrize("name", ["JWT_SECRET_KEY", "ORDER_ACCESS_SECRET_KEY"])
@pytest.mark.parametrize("value", [None, "", "   ", "short-but-varied", "a" * 64, " strong-but-padded-secret-value-123456789 "])
def test_startup_rejects_missing_or_weak_secret(monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name) as error:
        with TestClient(app):
            pytest.fail("Application must not serve with invalid signing secrets")
    if value and value.strip():
        assert value not in str(error.value)


def test_startup_rejects_shared_secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", get_order_access_settings().secret_key)
    with pytest.raises(RuntimeError, match="must be different"):
        with TestClient(app):
            pytest.fail("Application must not serve with a shared signing key")


def test_startup_accepts_independent_dummy_secrets():
    with TestClient(app):
        pass


@pytest.mark.parametrize("name", ["ACCESS_TOKEN_EXPIRE_MINUTES", "ORDER_ACCESS_TOKEN_EXPIRE_MINUTES"])
@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_startup_rejects_invalid_token_lifetimes(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        with TestClient(app):
            pytest.fail("Application must not serve with an invalid token lifetime")


@pytest.mark.parametrize("purpose", [None, "order_access", "STAFF_ACCESS", "", 1, ["staff_access"]])
def test_staff_verifier_rejects_invalid_purpose(purpose):
    claims = {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=1)}
    if purpose is not None:
        claims["purpose"] = purpose
    token = jwt.encode(claims, get_jwt_settings().secret_key, algorithm=JWT_ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)


def test_both_verifiers_reject_cross_purpose_even_with_shared_dummy_key(monkeypatch):
    # Deliberately bypass startup here to prove purpose checks independently of keys.
    monkeypatch.setenv("JWT_SECRET_KEY", get_order_access_settings().secret_key)
    staff = create_access_token(1)
    guest = create_order_access_token(1)
    with pytest.raises(jwt.InvalidTokenError, match="Invalid token purpose"):
        decode_access_token(guest)
    with pytest.raises(jwt.InvalidTokenError, match="Invalid token purpose"):
        decode_order_access_token(staff)


@pytest.mark.parametrize("role,path", [(UserRole.KITCHEN, "/kitchen/orders"), (UserRole.RECEPTION, "/payments/pending")])
def test_guest_cannot_impersonate_staff_with_matching_ids(client, session, monkeypatch, role, path):
    user = User(username="isolation-staff", password_hash="unused", role=role)
    session.add(user)
    session.commit()
    # Startup already validated distinct dummy keys. Simulate a misconfiguration
    # afterward so this checks the dependency's purpose rejection, not signature.
    monkeypatch.setenv("ORDER_ACCESS_SECRET_KEY", get_jwt_settings().secret_key)
    response = client.get(path, headers={"Authorization": f"Bearer {create_order_access_token(user.id)}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_staff_cannot_access_guest_order_even_with_matching_id_and_key(client, session, monkeypatch):
    order = Order(total=Decimal("1.00"))
    session.add(order)
    session.commit()
    monkeypatch.setenv("JWT_SECRET_KEY", get_order_access_settings().secret_key)
    response = client.get(f"/orders/{order.id}", headers={"X-Order-Access-Token": create_access_token(order.id)})
    assert response.status_code == 401


def test_legacy_staff_token_without_purpose_is_unauthorized(client, session):
    user = User(username="legacy-staff", password_hash="unused", role=UserRole.KITCHEN)
    session.add(user)
    session.commit()
    token = jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(minutes=1)},
                       get_jwt_settings().secret_key, algorithm=JWT_ALGORITHM)
    assert client.get("/kitchen/orders", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("claim", ["sub", "exp", "purpose"])
@pytest.mark.parametrize("missing", [True, False])
def test_staff_required_claims_cannot_be_missing_or_null(claim, missing):
    claims = {"sub": "1", "purpose": "staff_access", "exp": datetime.now(timezone.utc) + timedelta(minutes=1)}
    if missing:
        claims.pop(claim)
    else:
        claims[claim] = None
    token = jwt.encode(claims, get_jwt_settings().secret_key, algorithm=JWT_ALGORITHM)
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)


def test_startup_accepts_minimum_length_independent_dummy_secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "0123456789abcdefghijklmnopqrstuv")
    monkeypatch.setenv("ORDER_ACCESS_SECRET_KEY", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    with TestClient(app):
        pass
