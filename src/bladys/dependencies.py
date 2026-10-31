"""Request authentication dependencies."""

import jwt
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from bladys.database import get_session
from bladys.models import Order, User, UserRole
from bladys.tokens import decode_access_token
from bladys.order_tokens import decode_order_access_token


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    unauthorized = HTTPException(
        status_code=401, detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise unauthorized from None
    user = session.get(User, user_id)
    if user is None:
        raise unauthorized
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


def require_role(required_role: UserRole):
    """Authorize using the active user's current database role."""
    def authorize(user: User = Depends(get_current_user)) -> User:
        if user.role != required_role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return authorize


def get_owned_order(
    order_id: int,
    order_access_token: str | None = Header(default=None, alias="X-Order-Access-Token"),
    session: Session = Depends(get_session),
) -> Order:
    """Return an Order only after its capability matches the requested ID."""
    unauthorized = HTTPException(status_code=401, detail="Invalid Order access credentials")
    if not order_access_token:
        raise unauthorized
    try:
        token_order_id = decode_order_access_token(order_access_token)
    except jwt.InvalidTokenError:
        raise unauthorized from None
    if token_order_id != order_id:
        raise HTTPException(status_code=403, detail="Order access denied")
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
