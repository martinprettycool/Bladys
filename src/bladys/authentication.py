"""Internal credential authentication, independent of token creation."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from bladys.models import User
from bladys.security import verify_password


def authenticate_user(
    session: Session, username: str, plain_password: str,
) -> User | None:
    """Return an active user for valid credentials, or None for authentication failure."""
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        return None
    if not verify_password(plain_password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user
