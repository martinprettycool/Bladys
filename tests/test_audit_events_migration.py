from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from bladys.models import AuditEvent


def test_audit_events_migration(postgres_engine, monkeypatch):
    """Contexto: se agrega auditoria a una base con pedidos, pagos y empleados.
    Garantia: cada pago auditado pertenece al pedido del evento; se preservan las demas restricciones.
    Escenarios: upgrade, pago propio o ajeno, evento sin pago, referencias inexistentes y downgrade.
    """
    assert postgres_engine.dialect.name == "postgresql"
    assert postgres_engine.url.host in {"localhost", "127.0.0.1", "::1"}
    assert postgres_engine.url.database == "bladys_test"
    schema = "bladys_audit_" + uuid4().hex
    with postgres_engine.begin() as db:
        db.execute(CreateSchema(schema))
    migrated = create_engine(postgres_engine.url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        monkeypatch.setenv("ALEMBIC_TARGET", "test")
        monkeypatch.setenv("TEST_DATABASE_URL", migrated.url.render_as_string(hide_password=False))
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "0008")
        with migrated.begin() as db:
            db.execute(text("INSERT INTO orders (total) VALUES (6), (8)"))
            db.execute(text("INSERT INTO payments (order_id) VALUES (1), (2)"))
            db.execute(text("INSERT INTO users (username, password_hash, role) VALUES ('staff', 'unused', 'KITCHEN')"))
        command.upgrade(config, "0009")
        with migrated.begin() as db:
            for actor, user in [("CUSTOMER", None), ("SYSTEM", None), ("STAFF", 1)]:
                row = db.execute(text(
                    "INSERT INTO audit_events (order_id, payment_id, event_type, actor_type, actor_user_id) "
                    "VALUES (1, 1, 'TEST', :actor, :user) RETURNING id, changes, created_at"
                ), {"actor": actor, "user": user}).one()
                assert row.id > 0 and row.changes == {} and row.created_at.tzinfo is not None
            base = dict(order_id=1, payment_id=None, event_type="TEST", actor_type="SYSTEM", actor_user_id=None)
            invalid = [
                dict(actor_type="STAFF"), dict(actor_type="CUSTOMER", actor_user_id=1),
                dict(actor_user_id=1), dict(actor_type="OTHER"), dict(actor_type=None),
                dict(order_id=None), dict(order_id=999), dict(payment_id=999),
                dict(payment_id=2),
                dict(actor_type="STAFF", actor_user_id=999), dict(event_type=None),
            ]
            for override in invalid:
                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.execute(text(
                            "INSERT INTO audit_events (order_id, payment_id, event_type, actor_type, actor_user_id) "
                            "VALUES (:order_id, :payment_id, :event_type, :actor_type, :actor_user_id)"
                        ), base | override)
            # The rejected pair above uses two existing records from different orders.
            for order_id, payment_id in [(2, 2), (2, None)]:
                db.execute(text(
                    "INSERT INTO audit_events (order_id, payment_id, event_type, actor_type) "
                    "VALUES (:order_id, :payment_id, 'TEST', 'SYSTEM')"
                ), {"order_id": order_id, "payment_id": payment_id})
            with pytest.raises(IntegrityError) as mismatch:
                with db.begin_nested():
                    db.execute(text("UPDATE audit_events SET payment_id = 2 WHERE order_id = 1"))
            assert mismatch.value.orig.sqlstate == "23503"
            assert mismatch.value.orig.diag.constraint_name == "fk_audit_events_order_payment"
            for column in ("changes", "created_at"):
                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.execute(text(f"INSERT INTO audit_events (order_id, event_type, actor_type, {column}) VALUES (1, 'TEST', 'SYSTEM', NULL)"))
            for table in ("payments", "users", "orders"):
                with pytest.raises(IntegrityError):
                    with db.begin_nested():
                        db.execute(text(f"DELETE FROM {table} WHERE id = 1"))
            assert db.scalar(text("SELECT pg_typeof(id)::text FROM audit_events LIMIT 1")) == "bigint"
            assert db.scalar(text("SELECT pg_typeof(changes)::text FROM audit_events LIMIT 1")) == "jsonb"
            inspector = inspect(db)
            assert any(
                item["name"] == "uq_payments_order_id_id" and item["column_names"] == ["order_id", "id"]
                for item in inspector.get_unique_constraints("payments")
            )
            foreign_keys = inspector.get_foreign_keys("audit_events")
            payment_fk = next(item for item in foreign_keys if item["name"] == "fk_audit_events_order_payment")
            assert payment_fk["constrained_columns"] == ["order_id", "payment_id"]
            assert payment_fk["referred_columns"] == ["order_id", "id"]
            assert payment_fk["options"]["ondelete"] == "RESTRICT"
            assert not any(item["constrained_columns"] == ["payment_id"] for item in foreign_keys)
            assert inspect(db).get_indexes("audit_events")[0]["column_names"] == ["order_id", "created_at", "id"]
        with Session(migrated) as session:
            event = AuditEvent(order_id=1, event_type="ORM_TEST", actor_type="SYSTEM")
            session.add(event)
            session.commit()
            assert event.id > 0 and event.changes == {} and event.created_at.tzinfo is not None
        command.downgrade(config, "0008")
        with migrated.connect() as db:
            assert not inspect(db).has_table("audit_events")
            assert "uq_payments_order_id_id" not in {
                item["name"] for item in inspect(db).get_unique_constraints("payments")
            }
            assert any(item["column_names"] == ["order_id"] for item in inspect(db).get_unique_constraints("payments"))
            assert inspect(db).get_pk_constraint("payments")["constrained_columns"] == ["id"]
            for table, count in [("orders", 2), ("payments", 2), ("users", 1)]:
                assert db.scalar(text(f"SELECT count(*) FROM {table}")) == count
        command.upgrade(config, "0009")
        with migrated.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM audit_events")) == 0
            with pytest.raises(IntegrityError) as mismatch:
                with db.begin_nested():
                    db.execute(text("INSERT INTO audit_events (order_id, payment_id, event_type, actor_type) VALUES (1, 2, 'TEST', 'SYSTEM')"))
            assert mismatch.value.orig.sqlstate == "23503"
            assert mismatch.value.orig.diag.constraint_name == "fk_audit_events_order_payment"
    finally:
        migrated.dispose()
        with postgres_engine.begin() as db:
            db.execute(DropSchema(schema, cascade=True))
