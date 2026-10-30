from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bladys.config import JWT_ALGORITHM, get_jwt_settings
from bladys.database import get_session
from bladys.dependencies import get_current_user
from bladys.models import User, UserRole
from bladys.tokens import create_access_token


@pytest.fixture(autouse=True)
def jwt_environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-not-for-production-123456789")
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)


@pytest.fixture
def current_user(session):
    user = User(username="staff", password_hash="unused", role=UserRole.KITCHEN)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def auth_client(engine):
    app = FastAPI()

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    @app.get("/current-user")
    def current_user_route(user: User = Depends(get_current_user)):
        assert isinstance(user, User)
        return {"id": user.id, "username": user.username}

    with TestClient(app) as client:
        yield client


def signed_token(claims):
    return jwt.encode(claims, get_jwt_settings().secret_key, algorithm=JWT_ALGORITHM)


def test_valid_bearer_returns_expected_user(auth_client, current_user):
    response = auth_client.get("/current-user", headers={
        "Authorization": f"Bearer {create_access_token(current_user.id)}",
    })
    assert response.status_code == 200
    assert response.json() == {"id": current_user.id, "username": current_user.username}


@pytest.mark.parametrize("authorization", [None, "Basic credentials", "Bearer", "Bearer malformed"])
def test_missing_or_malformed_credentials_return_401(auth_client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = auth_client.get("/current-user", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_tampered_token_returns_401(auth_client, current_user):
    token = create_access_token(current_user.id)
    other_token = create_access_token(current_user.id + 1)
    header, _, signature = token.split(".")
    tampered = f"{header}.{other_token.split('.')[1]}.{signature}"
    response = auth_client.get("/current-user", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_expired_token_returns_401(auth_client, current_user):
    token = signed_token({
        "sub": str(current_user.id),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    })
    response = auth_client.get("/current-user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_nonexistent_user_returns_401(auth_client, current_user):
    token = create_access_token(current_user.id + 1)
    response = auth_client.get("/current-user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_inactive_user_returns_403(auth_client, session, current_user):
    token = create_access_token(current_user.id)
    current_user.is_active = False
    session.commit()
    response = auth_client.get("/current-user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


@pytest.mark.parametrize("subject", [None, "abc", "1.5", "-1", "0", "99999999999999999999", 1])
def test_invalid_subject_returns_401(auth_client, subject):
    token = signed_token({"sub": subject, "exp": datetime.now(timezone.utc) + timedelta(minutes=1)})
    response = auth_client.get("/current-user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("claims", [{"sub": "1"}, {"exp": 9999999999}])
def test_missing_required_claim_returns_401(auth_client, claims):
    token = signed_token(claims)
    response = auth_client.get("/current-user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
