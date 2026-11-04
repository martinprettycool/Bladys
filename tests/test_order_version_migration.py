from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema


def test_order_version_migration_backfills_and_enforces_constraints(postgres_engine, monkeypatch):
    """Contexto: una base existente contiene pedidos anteriores al control de version.
    Garantia: migrar preserva los pedidos, asigna version 1 y exige versiones positivas no nulas.
    Escenarios: cadena hasta 0007, upgrade 0008, inserciones invalidas y downgrade de 0008.
    """
    schema = "bladys_version_" + uuid4().hex
    with postgres_engine.begin() as db:
        db.execute(CreateSchema(schema))
    migrated = create_engine(postgres_engine.url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        monkeypatch.setenv("ALEMBIC_TARGET", "test")
        monkeypatch.setenv("TEST_DATABASE_URL", migrated.url.render_as_string(hide_password=False))
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "0007")
        with migrated.begin() as db:
            db.execute(text("INSERT INTO orders (total) VALUES (6.00)"))
        command.upgrade(config, "head")
        with migrated.begin() as db:
            assert db.execute(text("SELECT total, version FROM orders")).one() == (6, 1)
            db.execute(text("INSERT INTO orders (total) VALUES (9.00)"))
            assert db.scalar(text("SELECT min(version) FROM orders")) == 1
            for invalid in [None, 0, -1]:
                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.execute(text("INSERT INTO orders (total, version) VALUES (1, :version)"), {"version": invalid})
        command.downgrade(config, "0007")
        with migrated.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM orders")) == 2
            assert not db.scalar(text("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=:schema AND table_name='orders' AND column_name='version')"), {"schema": schema})
    finally:
        migrated.dispose()
        with postgres_engine.begin() as db:
            db.execute(DropSchema(schema, cascade=True))
