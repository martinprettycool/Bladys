import pytest

from bladys.authentication import authenticate_user
from bladys.models import User, UserRole
from bladys.security import hash_password


@pytest.fixture
def stored_user(session):
    user = User(
        username="kitchen-staff",
        password_hash=hash_password("test-only-password"),
        role=UserRole.KITCHEN,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    session.expunge(user)
    return user


def test_existing_user_with_correct_password_authenticates(session, stored_user):
    authenticated = authenticate_user(session, stored_user.username, "test-only-password")

    assert isinstance(authenticated, User)
    assert authenticated.id == stored_user.id
    assert authenticated.username == stored_user.username
    assert authenticated.is_active is True


def test_existing_user_with_wrong_password_fails(session, stored_user):
    assert authenticate_user(session, stored_user.username, "wrong-password") is None


def test_unknown_username_fails(session, stored_user):
    assert authenticate_user(session, "unknown-staff", "test-only-password") is None


def test_inactive_user_with_correct_password_fails(session, stored_user):
    user = session.get(User, stored_user.id)
    user.is_active = False
    session.commit()
    session.expunge_all()

    assert authenticate_user(session, stored_user.username, "test-only-password") is None
