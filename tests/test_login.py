import jwt
import pytest

from bladys.config import JWT_ALGORITHM, get_jwt_settings
from bladys.models import User, UserRole
from bladys.security import hash_password


@pytest.fixture(autouse=True)
def jwt_environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-not-for-production-123456789")
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)


@pytest.fixture
def login_user(session):
    user = User(
        username="login-staff",
        password_hash=hash_password("test-only-password"),
        role=UserRole.KITCHEN,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_active_user_with_correct_password_receives_access_token(client, login_user):
    response = client.post("/login", json={
        "username": login_user.username,
        "password": "test-only-password",
    })

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type"}
    assert isinstance(body["access_token"], str)
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    claims = jwt.decode(
        body["access_token"], get_jwt_settings().secret_key,
        algorithms=[JWT_ALGORITHM],
    )
    assert claims["sub"] == str(login_user.id)


@pytest.mark.parametrize("failure", ["wrong_password", "unknown_username", "inactive_user"])
def test_failed_login_returns_generic_error_without_token(client, session, login_user, failure):
    username = login_user.username
    password = "test-only-password"
    if failure == "wrong_password":
        password = "wrong-password"
    elif failure == "unknown_username":
        username = "unknown-staff"
    else:
        login_user.is_active = False
        session.commit()

    response = client.post("/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password"}
    assert "access_token" not in response.json()
