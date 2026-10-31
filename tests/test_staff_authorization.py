from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from sqlalchemy import select

from bladys.config import JWT_ALGORITHM, get_jwt_settings
from bladys.order_tokens import create_order_access_token
from bladys.models import Order, Payment, User, UserRole


OPERATIONS = [
    ("GET", "/payments/pending", None, UserRole.RECEPTION),
    ("PATCH", "/payments/{payment_id}/status", "APROBADO", UserRole.RECEPTION),
    ("PATCH", "/payments/{payment_id}/status", "RECHAZADO", UserRole.RECEPTION),
    ("GET", "/kitchen/orders/pending", None, UserRole.KITCHEN),
    ("PATCH", "/kitchen/orders/{order_id}/status", "PREPARANDO", UserRole.KITCHEN),
]


@pytest.fixture
def staff_order(session):
    order = Order(total=Decimal("0.00"), payment=Payment(status="PENDIENTE_VERIFICACION"))
    session.add(order)
    session.commit()
    return order


@pytest.mark.parametrize("method,path,target,role", OPERATIONS)
@pytest.mark.parametrize("identity,expected", [
    ("missing", 401), ("invalid", 401), ("expired", 401),
    ("inactive", 403), ("wrong_role", 403), ("correct_role", 200),
])
def test_staff_operations_authorization(
    client, session, staff_headers, staff_order, method, path, target, role, identity, expected,
):
    headers = staff_headers[role]
    if identity == "missing":
        headers = {}
    elif identity == "invalid":
        headers = {"Authorization": "Bearer malformed"}
    elif identity == "expired":
        user = session.scalar(select(User).where(User.role == role))
        token = jwt.encode({
            "sub": str(user.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        }, get_jwt_settings().secret_key, algorithm=JWT_ALGORITHM)
        headers = {"Authorization": f"Bearer {token}"}
    elif identity == "inactive":
        user = session.scalar(select(User).where(User.role == role))
        user.is_active = False
        session.commit()
    elif identity == "wrong_role":
        other_role = UserRole.KITCHEN if role == UserRole.RECEPTION else UserRole.RECEPTION
        headers = staff_headers[other_role]

    if role == UserRole.KITCHEN:
        staff_order.payment.status = "APROBADO"
        session.commit()
    order_id, payment_id = staff_order.id, staff_order.payment.id
    session.rollback()
    response = client.request(
        method, path.format(order_id=order_id, payment_id=payment_id), headers=headers,
        **({"json": {"status": target}} if target else {}),
    )
    assert response.status_code == expected
    if expected == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    session.expire_all()
    if expected != 200:
        assert session.get(Order, order_id).status == "PENDIENTE_ACEPTACION"
        assert session.get(Payment, payment_id).status == (
            "APROBADO" if role == UserRole.KITCHEN else "PENDIENTE_VERIFICACION"
        )
    elif method == "GET":
        assert [item["id"] for item in response.json()] == [
            order_id if role == UserRole.KITCHEN else payment_id
        ]
    elif role == UserRole.RECEPTION:
        assert session.get(Payment, payment_id).status == target
        assert session.get(Order, order_id).status == (
            "CANCELADO" if target == "RECHAZADO" else "PENDIENTE_ACEPTACION"
        )
    else:
        assert session.get(Order, order_id).status == target


@pytest.mark.parametrize("target", ["PREPARANDO", "LISTO", "ENVIADO", "RECHAZADO"])
def test_customer_route_cannot_change_staff_states(client, session, staff_order, target):
    response = client.patch(f"/orders/{staff_order.id}/status", json={"status": target},
                            headers={"X-Order-Access-Token": create_order_access_token(staff_order.id)})
    assert response.status_code == 422
    session.refresh(staff_order)
    assert staff_order.status == "PENDIENTE_ACEPTACION"


@pytest.mark.parametrize("target", ["CANCELADO", "RECHAZADO", "PENDIENTE_ACEPTACION"])
def test_kitchen_route_rejects_other_targets(client, staff_headers, staff_order, target):
    response = client.patch(f"/kitchen/orders/{staff_order.id}/status",
                            headers=staff_headers[UserRole.KITCHEN], json={"status": target})
    assert response.status_code == 422


def test_role_changes_apply_to_existing_token(client, session, staff_headers):
    headers = staff_headers[UserRole.KITCHEN]
    assert client.get("/kitchen/orders/pending", headers=headers).status_code == 200
    user = session.scalar(select(User).where(User.role == UserRole.KITCHEN))
    user.role = UserRole.RECEPTION
    session.commit()
    assert client.get("/kitchen/orders/pending", headers=headers).status_code == 403
    assert client.get("/payments/pending", headers=headers).status_code == 200
