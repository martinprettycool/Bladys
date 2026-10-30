import base64
import json
from datetime import datetime, timezone

import jwt
import pytest

from bladys.config import JWT_ALGORITHM, get_jwt_settings
from bladys.tokens import create_access_token


@pytest.fixture(autouse=True)
def jwt_environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-not-for-production-123456789")
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)


@pytest.fixture
def token():
    return create_access_token(42)


@pytest.fixture
def claims(token):
    return jwt.decode(token, get_jwt_settings().secret_key, algorithms=[JWT_ALGORITHM])


def test_token_can_be_created_for_user_id(token):
    assert isinstance(token, str)
    assert len(token.split(".")) == 3
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_signed_subject_is_user_id_string(claims):
    assert claims["sub"] == "42"
    assert set(claims) == {"sub", "iat", "exp"}


def test_token_contains_expiration(claims):
    assert isinstance(claims["exp"], int)


def test_token_contains_issued_at_timestamp(claims):
    assert isinstance(claims["iat"], int)
    assert abs(datetime.now(timezone.utc).timestamp() - claims["iat"]) < 5


def test_expiration_follows_issuance_by_default_lifetime(claims):
    assert claims["exp"] > claims["iat"]
    assert claims["exp"] - claims["iat"] == 30 * 60


def test_modified_payload_fails_signature_verification(token):
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = "99"
    modified_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    modified_token = f"{header}.{modified_payload}.{signature}"

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(modified_token, get_jwt_settings().secret_key, algorithms=[JWT_ALGORITHM])


def test_configured_lifetime_is_used(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10")
    token = create_access_token(42)
    claims = jwt.decode(token, get_jwt_settings().secret_key, algorithms=[JWT_ALGORITHM])

    assert claims["exp"] - claims["iat"] == 10 * 60


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_secret_is_required(monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET_KEY", secret)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be configured"):
        create_access_token(42)


@pytest.mark.parametrize("lifetime", ["0", "-1", "invalid"])
def test_invalid_lifetime_is_rejected(monkeypatch, lifetime):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", lifetime)

    with pytest.raises(RuntimeError, match="ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer"):
        create_access_token(42)
