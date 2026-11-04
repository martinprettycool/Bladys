"""Run the same browser flow using PostgreSQL in a disposable, isolated schema."""
import os
from pathlib import Path
import subprocess
import uuid

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.schema import CreateSchema, DropSchema


def main():
    """Contexto: un invitado confirma pizza y alitas desde un navegador movil.
    Garantia: el pedido, sabores, pago e idempotencia se persisten en PostgreSQL.
    Escenarios: doble clic, perdida de respuesta, reintento y recarga con token.
    """
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is required; no PostgreSQL verification ran")
    engine = create_engine(url)
    if engine.dialect.name != "postgresql":
        raise SystemExit("TEST_DATABASE_URL must use PostgreSQL")
    schema = "bladys_browser_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    env = dict(os.environ, BLADYS_BROWSER_DATABASE_URL=url, BLADYS_BROWSER_SCHEMA=schema)
    try:
        subprocess.run(["node", "frontend/tests/browser.mjs"], env=env, check=True)
        with engine.connect() as connection:
            # Only this randomly named schema is inspected or removed.
            connection.execute(text(f'SET search_path TO "{schema}"'))
            assert connection.scalar(text("SELECT count(*) FROM orders")) == 1
            assert connection.scalar(text("SELECT count(*) FROM payments")) == 1
            assert connection.scalar(text("SELECT count(*) FROM idempotency_records")) == 1
            assert connection.scalar(text("SELECT count(*) FROM order_items WHERE json_array_length(selected_flavors::json) > 0")) == 2
        print("PASS PostgreSQL: exactly one persisted Order, Payment and idempotency record; two flavor selections")
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


if __name__ == "__main__":
    main()
