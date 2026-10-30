import pytest

from bladys.security import hash_password, verify_password


@pytest.fixture
def password():
    return "test-only-password"


@pytest.fixture
def password_hash(password):
    return hash_password(password)


def test_hash_is_argon2_and_not_plaintext(password, password_hash):
    assert password_hash != password
    assert password_hash.startswith("$argon2id$")


def test_correct_password_verifies(password, password_hash):
    assert verify_password(password, password_hash) is True


def test_incorrect_password_does_not_verify(password_hash):
    assert verify_password("different-test-password", password_hash) is False


def test_same_password_produces_different_hashes(password, password_hash):
    assert hash_password(password) != password_hash


def test_both_independently_generated_hashes_verify(password, password_hash):
    second_hash = hash_password(password)

    assert verify_password(password, password_hash) is True
    assert verify_password(password, second_hash) is True
