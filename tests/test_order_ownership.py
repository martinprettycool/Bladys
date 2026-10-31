from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from bladys.config import JWT_ALGORITHM, get_order_access_settings
from bladys.database import get_session
from bladys.dependencies import get_owned_order
from bladys.models import Order
from bladys.order_tokens import create_order_access_token


@pytest.fixture
def ownership_client(engine):
    app = FastAPI()

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    @app.get("/orders/{order_id}")
    def owned_order_route(order: Order = Depends(get_owned_order)):
        assert isinstance(order, Order)
        return {"id": order.id, "total": str(order.total)}

    with TestClient(app) as client:
        yield client


@pytest.fixture
def orders(session):
    orders = [Order(total=Decimal("3.00")), Order(total=Decimal("6.00"))]
    session.add_all(orders)
    session.commit()
    return orders


def test_matching_token_returns_loaded_order(ownership_client, orders):
    order = orders[0]
    response = ownership_client.get(f"/orders/{order.id}", headers={
        "X-Order-Access-Token": create_order_access_token(order.id),
    })
    assert response.status_code == 200
    assert response.json() == {"id": order.id, "total": "3.00"}


@pytest.mark.parametrize("resource_exists", [True, False])
def test_other_order_token_returns_403(ownership_client, orders, resource_exists):
    order_a, order_b = orders
    route_id = order_b.id if resource_exists else order_b.id + 1
    response = ownership_client.get(f"/orders/{route_id}", headers={
        "X-Order-Access-Token": create_order_access_token(order_a.id),
    })
    assert response.status_code == 403


@pytest.mark.parametrize("header_value", [None, "", "malformed", "a.b.c"])
@pytest.mark.parametrize("resource_exists", [True, False])
def test_missing_or_malformed_credential_returns_401(
    ownership_client, orders, header_value, resource_exists,
):
    route_id = orders[0].id if resource_exists else orders[-1].id + 1
    headers = {} if header_value is None else {"X-Order-Access-Token": header_value}
    response = ownership_client.get(f"/orders/{route_id}", headers=headers)
    assert response.status_code == 401


def test_tampered_token_returns_401(ownership_client, orders):
    token = create_order_access_token(orders[0].id)
    other_payload = create_order_access_token(orders[1].id).split(".")[1]
    header, _, signature = token.split(".")
    tampered = f"{header}.{other_payload}.{signature}"
    response = ownership_client.get(f"/orders/{orders[1].id}", headers={
        "X-Order-Access-Token": tampered,
    })
    assert response.status_code == 401


def test_expired_token_returns_401(ownership_client, orders):
    token = jwt.encode({
        "sub": str(orders[0].id), "purpose": "order_access",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }, get_order_access_settings().secret_key, algorithm=JWT_ALGORITHM)
    response = ownership_client.get(f"/orders/{orders[0].id}", headers={
        "X-Order-Access-Token": token,
    })
    assert response.status_code == 401


def test_valid_matching_token_for_nonexistent_order_returns_404(ownership_client, orders):
    missing_id = orders[-1].id + 1
    response = ownership_client.get(f"/orders/{missing_id}", headers={
        "X-Order-Access-Token": create_order_access_token(missing_id),
    })
    assert response.status_code == 404


def test_order_token_in_authorization_header_is_not_ownership_credential(ownership_client, orders):
    response = ownership_client.get(f"/orders/{orders[0].id}", headers={
        "Authorization": f"Bearer {create_order_access_token(orders[0].id)}",
    })
    assert response.status_code == 401
