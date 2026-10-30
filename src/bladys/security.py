"""Password hashing and verification for internal authentication."""

from pwdlib import PasswordHash


_password_hash = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    """Return an Argon2 hash containing its salt and verification parameters."""
    return _password_hash.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Check a password against a library-generated hash."""
    return _password_hash.verify(plain_password, password_hash)
