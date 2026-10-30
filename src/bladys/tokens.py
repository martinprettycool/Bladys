"""JWT access-token creation and verification."""

from datetime import datetime, timedelta, timezone

import jwt

from bladys.config import JWT_ALGORITHM, get_jwt_settings


def create_access_token(user_id: int) -> str:
    """Sign a user ID and UTC issuance/expiration times using configured settings."""
    settings = get_jwt_settings()
    issued_at = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> int:
    """Verify an access token before interpreting its subject as a user ID."""
    settings = get_jwt_settings()
    claims = jwt.decode(
        token, settings.secret_key, algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    subject = claims["sub"]
    # User.id is a PostgreSQL INTEGER; reject invalid IDs before querying it.
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
        raise jwt.InvalidTokenError("Invalid user ID")
    if len(subject) > 10 or not 1 <= int(subject) <= 2147483647:
        raise jwt.InvalidTokenError("Invalid user ID")
    return int(subject)
