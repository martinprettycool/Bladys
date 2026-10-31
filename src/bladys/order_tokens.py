"""Signed guest Order capabilities, separate from staff identity tokens."""

from datetime import datetime, timedelta, timezone

import jwt

from bladys.config import JWT_ALGORITHM, get_order_access_settings


def create_order_access_token(order_id: int) -> str:
    settings = get_order_access_settings()
    issued_at = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(order_id),
            "purpose": "order_access",
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=settings.token_expire_minutes),
        },
        settings.secret_key,
        algorithm=JWT_ALGORITHM,
    )


def decode_order_access_token(token: str) -> int:
    """Verify an Order capability before returning its trusted Order ID.

    Invalid tokens raise jwt.InvalidTokenError (or a subclass).
    """
    settings = get_order_access_settings()
    claims = jwt.decode(
        token, settings.secret_key, algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "exp", "purpose"]},
    )
    if claims["purpose"] != "order_access":
        raise jwt.InvalidTokenError("Invalid token purpose")
    subject = claims["sub"]
    # Order.id is a PostgreSQL INTEGER; reject invalid IDs before any lookup.
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
        raise jwt.InvalidTokenError("Invalid Order ID")
    if len(subject) > 10 or not 1 <= int(subject) <= 2147483647:
        raise jwt.InvalidTokenError("Invalid Order ID")
    return int(subject)
