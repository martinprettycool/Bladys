import os

# Force SQLite before application imports, even when an external URL is set.
os.environ["DATABASE_URL"] = "sqlite://"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from bladys.database import get_session
from bladys.main import app
from bladys.models import Base, Product, ProductVariant
import pytest
from sqlalchemy import text
from dotenv import load_dotenv
from decimal import Decimal


load_dotenv()


@pytest.fixture
def product_factory():
    def create_product(**kwargs) ->  Product:
        defaults = {
            "name" : "Pizza",
            "is_available" : True,
            "variants" : []
        }
        return Product(**{**defaults, **kwargs})
    return create_product

@pytest.fixture
def variant_factory():
    def create_variant(
        *,
        name: str = "Personal",
        price: Decimal = Decimal("3.00"),
        is_available: bool = True,
    ) -> ProductVariant:
        return ProductVariant(
            name=name,
            price=price,
            is_available=is_available,
        )

    return create_variant

@pytest.fixture
def postgres_engine():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def postgres_session(
    postgres_engine,
    clean_postgres_db,
):
    with Session(postgres_engine) as session:
        yield session

@pytest.fixture
def postgres_client(
    postgres_engine,
    clean_postgres_db,
):
    def override_session():
        with Session(postgres_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)



@pytest.fixture
def clean_postgres_db(postgres_engine):
    tables = """
        users,
        payments,
        idempotency_records,
        order_items,
        orders,
        product_variants,
        products
    """

    # SETUP: garantizar que empezamos limpios
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                TRUNCATE TABLE {tables}
                RESTART IDENTITY
                CASCADE
                """
            )
        )

    yield

    # TEARDOWN: no dejar basura al siguiente test
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                TRUNCATE TABLE {tables}
                RESTART IDENTITY
                CASCADE
                """
            )
        )



@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine):
    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def staff_headers(session, monkeypatch):
    from bladys.models import User, UserRole
    from bladys.tokens import create_access_token

    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-not-for-production-123456789")
    users = [User(username=f"staff-{role}", password_hash="unused", role=role)
             for role in UserRole]
    session.add_all(users)
    session.commit()
    return {user.role: {"Authorization": f"Bearer {create_access_token(user.id)}"}
            for user in users}


@pytest.fixture
def postgres_staff_headers(postgres_session, monkeypatch):
    from bladys.models import User, UserRole
    from bladys.tokens import create_access_token

    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-not-for-production-123456789")
    users = [User(username=f"staff-{role}", password_hash="unused", role=role)
             for role in UserRole]
    postgres_session.add_all(users)
    postgres_session.commit()
    headers = {user.role: {"Authorization": f"Bearer {create_access_token(user.id)}"}
               for user in users}
    postgres_session.rollback()
    return headers


@pytest.fixture(autouse=True)
def order_access_environment(monkeypatch):
    monkeypatch.setenv("ORDER_ACCESS_SECRET_KEY", "test-only-order-capability-secret-123456789")
    monkeypatch.delenv("ORDER_ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
