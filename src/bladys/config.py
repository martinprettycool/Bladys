"""Environment configuration for access-token creation."""

import os
import secrets
from pathlib import Path
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


def validate_signing_secrets() -> None:
    """Fail startup on missing, obviously weak or shared HS256 signing keys.

    Require at least 32 UTF-8 bytes and eight distinct characters. These checks
    catch configuration mistakes, not entropy: generate each key independently
    with a cryptographically secure generator (for example token_urlsafe(32)).
    Never include secret values in configuration errors.
    """
    staff = get_jwt_settings().secret_key
    guest = get_order_access_settings().secret_key
    for name, value in (("JWT_SECRET_KEY", staff), ("ORDER_ACCESS_SECRET_KEY", guest)):
        if len(value.encode("utf-8")) < 32 or len(set(value)) < 8 or value != value.strip():
            raise RuntimeError(
                f"{name} must contain at least 32 UTF-8 bytes and 8 distinct characters, "
                "without surrounding whitespace; generate a cryptographically random secret"
            )
    if secrets.compare_digest(staff.encode("utf-8"), guest.encode("utf-8")):
        raise RuntimeError("JWT_SECRET_KEY and ORDER_ACCESS_SECRET_KEY must be different")


# Source checkout root; never derive storage from the process working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_payment_proofs_dir() -> Path:
    """Resolve PAYMENT_PROOFS_DIR; production requires explicit absolute storage."""
    configured = os.environ.get("PAYMENT_PROOFS_DIR")
    production = os.environ.get("APP_ENV", "development").lower() == "production"
    if configured is not None and not configured.strip():
        raise RuntimeError("PAYMENT_PROOFS_DIR must not be empty")
    directory = Path(configured) if configured is not None else Path("media/payment_proofs")
    if production and (configured is None or not directory.is_absolute()):
        raise RuntimeError("PAYMENT_PROOFS_DIR must be an explicit absolute path in production")
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    return directory.resolve()
