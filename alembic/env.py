import os

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from bladys.models import Base



load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada")


target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,
    )

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
