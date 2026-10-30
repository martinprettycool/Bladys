import pytest
from sqlalchemy import insert, null, select, text
from sqlalchemy.exc import IntegrityError

from bladys.models import User, UserRole


def test_duplicate_usernames_are_rejected(session):
    session.add(User(username="staff", password_hash="stored-hash", role=UserRole.KITCHEN))
    session.commit()
    session.add(User(username="staff", password_hash="other-hash", role=UserRole.RECEPTION))

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("field", ["username", "password_hash", "role", "is_active"])
def test_required_fields_reject_null(session, field):
    values = dict(username="staff", password_hash="stored-hash", role=UserRole.KITCHEN, is_active=True)
    values[field] = null()

    with pytest.raises(IntegrityError):
        session.execute(insert(User.__table__).values(**values))
        session.commit()
    session.rollback()


@pytest.mark.parametrize("role", [UserRole.KITCHEN, UserRole.RECEPTION])
def test_user_role_persists(session, role):
    user = User(username="staff", password_hash="stored-hash", role=role)
    session.add(user)
    session.commit()
    user_id = user.id
    session.expunge_all()

    persisted = session.get(User, user_id)
    assert persisted.role == role
    assert persisted.username == "staff"
    assert persisted.password_hash == "stored-hash"


@pytest.mark.parametrize("use_orm", [True, False])
def test_is_active_defaults_to_true(session, use_orm):
    values = dict(username="staff", password_hash="stored-hash", role=UserRole.KITCHEN)
    if use_orm:
        session.add(User(**values))
    else:
        # Bypass Python defaults to exercise the database default directly.
        session.execute(
            text("INSERT INTO users (username, password_hash, role) VALUES (:username, :password_hash, :role)"),
            values,
        )
    session.commit()
    session.expunge_all()

    assert session.scalar(select(User)).is_active is True
