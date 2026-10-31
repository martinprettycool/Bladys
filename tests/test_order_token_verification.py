import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from bladys.config import JWT_ALGORITHM, get_order_access_settings
from bladys.order_tokens import create_order_access_token, decode_order_access_token
from bladys.tokens import create_access_token


@pytest.fixture
def claims():
    return {
        "sub": "42",
        "purpose": "order_access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }


def sign_claims(claims, *, secret=None, algorithm=JWT_ALGORITHM):
    return jwt.encode(
        claims, secret or get_order_access_settings().secret_key, algorithm=algorithm,
    )


@pytest.mark.parametrize("order_id", [1, 42, 2147483647])
def test_valid_order_access_token_returns_integer_order_id(order_id):
    result = decode_order_access_token(create_order_access_token(order_id))
    assert type(result) is int
    assert result == order_id


@pytest.mark.parametrize("token", ["", "malformed", "a.b.c"])
def test_malformed_token_is_rejected(token):
    with pytest.raises(jwt.InvalidTokenError):
        decode_order_access_token(token)


def test_tampered_token_is_rejected():
    header, payload, signature = create_order_access_token(42).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = "99"
    changed_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(jwt.InvalidSignatureError):
        decode_order_access_token(f"{header}.{changed_payload}.{signature}")


def test_expired_token_is_rejected(claims):
    claims["exp"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_order_access_token(sign_claims(claims))


def test_wrong_secret_is_rejected(claims):
    token = sign_claims(claims, secret="test-only-wrong-signing-secret-123456789")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_order_access_token(token)


@pytest.mark.parametrize("purpose", ["staff_access", "ORDER_ACCESS", "", 1, ["order_access"]])
def test_wrong_purpose_is_rejected(claims, purpose):
    claims["purpose"] = purpose
    with pytest.raises(jwt.InvalidTokenError):
        decode_order_access_token(sign_claims(claims))


@pytest.mark.parametrize("claim", ["sub", "exp", "purpose"])
@pytest.mark.parametrize("missing", [True, False])
def test_required_claims_cannot_be_missing_or_null(claims, claim, missing):
    if missing:
        claims.pop(claim)
    else:
        claims[claim] = None
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_order_access_token(sign_claims(claims))


@pytest.mark.parametrize("subject", [
    "", "abc", "1.5", "-1", "0", " 42", "42 ", "+42", "2147483648",
    "9" * 5000, "\u0664\u0662", 42, 1.5, True, [], {},
])
def test_invalid_order_id_is_rejected(claims, subject):
    claims["sub"] = subject
    with pytest.raises(jwt.InvalidTokenError):
        decode_order_access_token(sign_claims(claims))


def test_unconfigured_algorithm_is_rejected(claims, monkeypatch):
    monkeypatch.setenv("ORDER_ACCESS_SECRET_KEY", "test-only-order-secret-long-enough-for-HS384-1234567890123456789")
    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_order_access_token(sign_claims(claims, algorithm="HS384"))


def test_staff_jwt_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-staff-secret-distinct-from-order-123456789")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_order_access_token(create_access_token(42))


def test_staff_jwt_without_order_purpose_is_rejected_even_if_secrets_match(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", get_order_access_settings().secret_key)
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_order_access_token(create_access_token(42))
