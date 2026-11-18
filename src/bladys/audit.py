"""Persist audit events within the caller's transaction."""

import json
from typing import Literal

from sqlalchemy.orm import Session

from bladys.models import AuditEvent


type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]

EVENT_TYPES = frozenset({
    "ORDER_CREATED",
    "ORDER_ITEMS_REPLACED",
    "PAYMENT_PROOF_SUBMITTED",
    "PAYMENT_APPROVED",
    "PAYMENT_REJECTED",
    "ORDER_STATUS_CHANGED",
})


def record_audit_event(
    session: Session,
    *,
    order_id: int,
    event_type: str,
    actor_type: Literal["CUSTOMER", "STAFF", "SYSTEM"],
    payment_id: int | None = None,
    actor_user_id: int | None = None,
    changes: dict[str, JSONValue] | None = None,
) -> AuditEvent:
    """Add and return a pending event; never flush or finish the transaction.

    The caller must supply only safe audit fields, excluding credentials, JWTs,
    order tokens, sensitive data and proof contents. JSON validation does not
    detect secrets. PostgreSQL validates actors and references at flush time.
    The caller owns commit/rollback, including recovery after a failed flush.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported audit event type")
    if changes is not None and not isinstance(changes, dict):
        raise ValueError("Audit changes must be a JSON object")
    try:
        # Snapshot nested values so later caller mutations cannot alter the event.
        snapshot = json.loads(json.dumps({} if changes is None else changes, allow_nan=False))
    except (TypeError, ValueError):
        raise ValueError("Audit changes must contain JSON serializable values") from None

    event = AuditEvent(
        order_id=order_id,
        payment_id=payment_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        changes=snapshot,
    )
    session.add(event)
    return event
