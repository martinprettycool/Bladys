"""Environment configuration for access-token creation."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv()
JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class JWTSettings:
    secret_key: str = field(repr=False)
    access_token_expire_minutes: int = 30


def get_jwt_settings() -> JWTSettings:
    """Read JWT_SECRET_KEY and optional ACCESS_TOKEN_EXPIRE_MINUTES (default 30)."""
    secret_key = os.environ.get("JWT_SECRET_KEY")
    if not secret_key or not secret_key.strip():
        raise RuntimeError("JWT_SECRET_KEY must be configured")
    try:
        lifetime = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    except ValueError:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer") from None
    if lifetime <= 0:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer")
    return JWTSettings(secret_key=secret_key, access_token_expire_minutes=lifetime)


@dataclass(frozen=True)
class OrderAccessSettings:
    secret_key: str = field(repr=False)
    token_expire_minutes: int = 1440


def get_order_access_settings() -> OrderAccessSettings:
    """Read independent Order capability signing settings from the environment."""
    secret_key = os.environ.get("ORDER_ACCESS_SECRET_KEY")
    if not secret_key or not secret_key.strip():
        raise RuntimeError("ORDER_ACCESS_SECRET_KEY must be configured")
    try:
        lifetime = int(os.environ.get("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    except ValueError:
        raise RuntimeError("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer") from None
    if lifetime <= 0:
        raise RuntimeError("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer")
    return OrderAccessSettings(secret_key=secret_key, token_expire_minutes=lifetime)
