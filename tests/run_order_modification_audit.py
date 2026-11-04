"""Audit runner: existing destructive fixtures are confined to a disposable schema."""
import os
from pathlib import Path
import subprocess
import sys
import uuid
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema


def main():
    """Contexto: las fixtures existentes limpian tablas mediante TRUNCATE.
    Garantia: solo se modifican tablas de un esquema temporal de auditoria.
    Escenarios: ejecucion completa o fallida; siempre se elimina el esquema creado.
    """
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    if engine.dialect.name != "postgresql":
        raise SystemExit("PostgreSQL required")
    schema = "bladys_edit_audit_" + uuid.uuid4().hex
    with engine.begin() as db:
        db.execute(CreateSchema(schema))
    try:
        url = engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
        isolated = create_engine(url)
        os.environ["DATABASE_URL"] = "sqlite://"
        from bladys.models import Base
        Base.metadata.create_all(isolated)
        isolated.dispose()
        env = dict(os.environ, TEST_DATABASE_URL=url.render_as_string(hide_password=False))
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", *sys.argv[1:]]
        result = subprocess.run(command, cwd=root, env=env)
        return result.returncode
    finally:
        with engine.begin() as db:
            db.execute(DropSchema(schema, cascade=True))
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
