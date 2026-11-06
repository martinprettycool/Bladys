"""Explicit development-only staff provisioning: python -m bladys.seed_staff."""
import os
import re

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session

from bladys.models import User, UserRole
from bladys.security import hash_password

ACCOUNTS = (
    ("reception_test", UserRole.RECEPTION, "DEV_RECEPTION_PASSWORD"),
    ("kitchen_test", UserRole.KITCHEN, "DEV_KITCHEN_PASSWORD"),
)


def validate_target(environment, database_url):
    """Reject targets outside the explicit local development convention."""
    if environment.get("APP_ENV") != "development":
        raise ValueError("APP_ENV debe ser development.")
    if environment.get("BLADYS_ALLOW_DEV_STAFF_SEED") != "1":
        raise ValueError("BLADYS_ALLOW_DEV_STAFF_SEED debe ser 1.")
    try:
        url = make_url(database_url or "")
    except ArgumentError:
        raise ValueError("DATABASE_URL debe contener una URL PostgreSQL valida.") from None
    if (url.get_backend_name() != "postgresql"
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or (url.database != "bladys"
                and not re.search(r"(?:^|_)(?:dev|test)(?:_|$)", url.database or ""))
            or set(url.query) - {"options"}
            or ("options" in url.query and (not isinstance(url.query["options"], str) or not re.fullmatch(
                r"-csearch_path=[a-zA-Z_][a-zA-Z_0-9]*", url.query["options"])))):
        raise ValueError(
            "Seed permitido solo en PostgreSQL local (localhost/127.0.0.1/::1), "
            "base bladys o con segmento dev o test (ej. bladys_dev). "
            "Solo se permite options=-csearch_path=<esquema> como parametro URL."
        )
    # libpq environment variables must not silently redirect a nominally local URL.
    if any(environment.get(name) for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        raise ValueError("Retira PGHOST, PGHOSTADDR, PGSERVICE y PGSERVICEFILE antes del seed.")


def read_passwords(environment):
    passwords = {}
    for username, role, variable in ACCOUNTS:
        password = environment.get(variable)
        if not password or not password.strip():
            raise ValueError(f"Falta configurar {variable}; no se crearon usuarios.")
        passwords[username] = password
    return passwords


def seed_staff(session: Session, passwords: dict[str, str]) -> list[str]:
    """Insert missing accounts; the caller commits both accounts together."""
    created = []
    for username, role, variable in ACCOUNTS:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            result = session.execute(insert(User).values(
                username=username, role=role, is_active=True,
                password_hash=hash_password(passwords[username]),
            ).on_conflict_do_nothing(index_elements=[User.username]).returning(User.id))
            if result.scalar_one_or_none() is not None:
                created.append(username)
            user = session.scalar(select(User).where(User.username == username)
                                  .execution_options(populate_existing=True))
        if user.role != role or not user.is_active:
            raise ValueError(f"{username} ya existe con otro rol o esta inactivo; no se modifico.")
    return created


def main():
    load_dotenv()
    try:
        validate_target(os.environ, os.environ.get("DATABASE_URL"))
        passwords = read_passwords(os.environ)
        # Reuse the application's configured engine, only after checking the target.
        from bladys.database import engine
        validate_target(os.environ, engine.url)
        with Session(engine) as session, session.begin():
            created = seed_staff(session, passwords)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except SQLAlchemyError:
        # DB exceptions can contain connection details, SQL parameters or hashes.
        raise SystemExit("No se pudo provisionar personal. Revisa conexion y migraciones; transaccion revertida.") from None
    print(f"Seed de desarrollo completado: {len(created)} cuentas creadas; cuentas existentes conservadas.")


if __name__ == "__main__":
    main()
