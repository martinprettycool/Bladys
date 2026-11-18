from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from bladys.audit import EVENT_TYPES, record_audit_event
from bladys.models import AuditEvent, Order


@pytest.fixture
def audit_engine(postgres_engine, monkeypatch):
    assert postgres_engine.dialect.name == "postgresql"
    assert postgres_engine.url.host in {"localhost", "127.0.0.1", "::1"}
    assert postgres_engine.url.database == "bladys_test"
    schema = "bladys_audit_record_" + uuid4().hex
    with postgres_engine.begin() as db:
        db.execute(CreateSchema(schema))
    engine = create_engine(postgres_engine.url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        monkeypatch.setenv("ALEMBIC_TARGET", "test")
        monkeypatch.setenv("TEST_DATABASE_URL", engine.url.render_as_string(hide_password=False))
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "0009")
        with engine.begin() as db:
            db.execute(text("INSERT INTO orders (total) VALUES (6), (8)"))
            db.execute(text("INSERT INTO payments (order_id) VALUES (1), (2)"))
            db.execute(text("INSERT INTO users (username, password_hash, role) VALUES ('staff', 'unused', 'KITCHEN')"))
        yield engine
    finally:
        engine.dispose()
        with postgres_engine.begin() as db:
            db.execute(DropSchema(schema, cascade=True))


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_record_event_waits_for_caller_commit(audit_engine, event_type):
    """Contexto: un flujo agrega un evento antes de confirmar su trabajo.
    Garantia: add y flush no confirman; solo el commit del llamador hace visible el evento.
    Escenarios: evento pendiente, flush invisible desde otra conexion y commit persistente.
    """
    changes = {"status": {"before": "PENDIENTE_ACEPTACION", "after": "PREPARANDO"},
               "items": [1, True, None, 2.5]}
    with Session(audit_engine) as session:
        event = record_audit_event(session, order_id=1, payment_id=1,
                                   event_type=event_type, actor_type="STAFF",
                                   actor_user_id=1, changes=changes)
        assert event in session.new
        assert event.id is None
        changes["status"]["after"] = "CANCELADO"
        assert event.changes["status"]["after"] == "PREPARANDO"
        session.flush()
        assert event.id is not None and event.created_at.tzinfo is not None
        event_id = event.id
        with Session(audit_engine) as observer:
            assert observer.get(AuditEvent, event_id) is None
        session.commit()
    with Session(audit_engine) as observer:
        stored = observer.get(AuditEvent, event_id)
        assert stored.event_type == event_type
        assert (stored.order_id, stored.payment_id, stored.actor_user_id) == (1, 1, 1)
        assert stored.changes["status"]["after"] == "PREPARANDO"


@pytest.mark.parametrize("flush", [False, True])
def test_caller_rollback_removes_event(audit_engine, flush):
    """Contexto: un flujo abandona una operacion despues de registrar su auditoria.
    Garantia: rollback no deja el evento almacenado, incluso si se envio el INSERT.
    Escenarios: rollback antes y despues del flush, verificado desde otra sesion.
    """
    with Session(audit_engine) as session:
        event = record_audit_event(session, order_id=1, event_type="ORDER_CREATED", actor_type="CUSTOMER")
        assert event.changes == {} and event.payment_id is None
        if flush:
            session.flush()
        session.rollback()
    with Session(audit_engine) as observer:
        assert observer.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("invalid", [
    {"actor_type": "STAFF"},
    {"actor_type": "CUSTOMER", "actor_user_id": 1},
    {"actor_type": "SYSTEM", "actor_user_id": 1},
    {"actor_type": "OTHER"},
    {"actor_type": "STAFF", "actor_user_id": 999},
    {"order_id": 999},
    {"payment_id": 999},
    {"payment_id": 2},
])
def test_failed_event_and_business_change_roll_back_together(audit_engine, invalid):
    """Contexto: un pedido cambia pero el evento contiene un actor o referencia invalida.
    Garantia: el fallo de auditoria permite revertir tambien el cambio de negocio ya enviado.
    Escenarios: actores invalidos, referencias inexistentes y pago de otro pedido; rollback conjunto.
    """
    with Session(audit_engine) as session:
        order = session.get(Order, 1)
        order.total = Decimal("10.00")
        session.flush()
        args = dict(order_id=1, event_type="ORDER_ITEMS_REPLACED", actor_type="SYSTEM")
        record_audit_event(session, **(args | invalid))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    with Session(audit_engine) as observer:
        assert observer.get(Order, 1).total == Decimal("6.00")
        assert observer.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize("invalid", [
    {"event_type": "UNKNOWN"},
    {"changes": {"amount": Decimal("2.00")}},
    {"changes": {"proof": b"binary"}},
    {"changes": {"value": float("nan")}},
    {"changes": {"value": float("inf")}},
    {"changes": []},
])
def test_invalid_input_does_not_finish_caller_transaction(audit_engine, invalid):
    """Contexto: el llamador entrega un tipo desconocido o datos no serializables.
    Garantia: la validacion rechaza el evento sin confirmar ni revertir trabajo ajeno.
    Escenarios: error previo al add, cambio de negocio pendiente y rollback explicito.
    """
    with Session(audit_engine) as session:
        order = session.get(Order, 1)
        order.total = Decimal("10.00")
        args = dict(order_id=1, event_type="ORDER_CREATED", actor_type="SYSTEM")
        with pytest.raises(ValueError):
            record_audit_event(session, **(args | invalid))
        assert not session.new
        assert order in session.dirty and order.total == Decimal("10.00")
        with Session(audit_engine) as observer:
            assert observer.get(Order, 1).total == Decimal("6.00")
        session.rollback()
