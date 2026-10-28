import os
from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from bladys.models import Base


# ---------------------------------------------------------
# 1. Configuración base de Alembic
# ---------------------------------------------------------

config = context.config



load_dotenv()

target_metadata = Base.metadata


# ---------------------------------------------------------
# 2. Elegir qué database debe usar Alembic
# ---------------------------------------------------------

alembic_target = os.getenv("ALEMBIC_TARGET", "development").lower()

if alembic_target in {"development", "dev"}:
    database_url = os.environ["DATABASE_URL"]

elif alembic_target == "test":
    database_url = os.environ["TEST_DATABASE_URL"]

else:
    raise RuntimeError(
        f"Unknown ALEMBIC_TARGET: {alembic_target}"
    )


# ---------------------------------------------------------
# 3. Protección para no migrar accidentalmente la DB real
# ---------------------------------------------------------

if alembic_target == "test":
    url = make_url(database_url)
    database_name = url.database

    if database_name != "bladys_test":
        raise RuntimeError(
            "Refusing to run test migrations against "
            f"database '{database_name}'. Expected 'bladys_test'."
        )


# ---------------------------------------------------------
# 4. Darle a Alembic la URL elegida
# ---------------------------------------------------------

config.set_main_option(
    "sqlalchemy.url",
    database_url.replace("%", "%%"),
)


# ---------------------------------------------------------
# 5. Migraciones offline
# ---------------------------------------------------------

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------
# 6. Migraciones online
# ---------------------------------------------------------

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


# ---------------------------------------------------------
# 7. Alembic decide qué modo ejecutar
# ---------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()