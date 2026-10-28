from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Header
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError

from bladys.database import get_session
from bladys.models import Order, OrderItem, Product, ProductVariant, IdempotencyRecord
from bladys.schemas import OrderCreate, OrderResponse, OrderStatusUpdate, ProductResponse
from bladys.order_lifecycle import EXPECTED_STATUS
import hashlib
import json
from bladys.idempotency import build_request_hash

app = FastAPI()


@app.get("/products", response_model=list[ProductResponse])
def get_products(session: Session = Depends(get_session)):
    statement = (
        select(Product)
        .where(Product.is_available.is_(True))
        .options(
            selectinload(
                Product.variants.and_(ProductVariant.is_available.is_(True))
            )
        )
        .order_by(Product.id)
    )
    return session.scalars(statement).all()


def normalize_items(payload: OrderCreate) -> dict[int, int]:
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.variant_id] = quantities.get(item.variant_id, 0) + item.quantity
    return quantities


def build_order_items(session: Session, quantities: dict[int, int]):
    statement = (
        select(ProductVariant, Product.is_available)
        .join(ProductVariant.product)
        .where(ProductVariant.id.in_(quantities))
    )
    variants = {
        variant.id: (variant, product_available)
        for variant, product_available in session.execute(statement)
    }

    order_items = []
    total = Decimal("0.00")
    for variant_id, quantity in quantities.items():
        if variant_id not in variants:
            raise HTTPException(404, detail=f"La variante {variant_id} no existe")

        variant, product_available = variants[variant_id]
        if not variant.is_available or not product_available:
            raise HTTPException(400, detail=f"La variante {variant_id} no esta disponible")

        order_items.append(OrderItem(
            variant_id=variant_id,
            quantity=quantity,
            unit_price=variant.price,
        ))
        total += variant.price * quantity

    return order_items, total


@app.post("/orders", response_model=OrderResponse, status_code=201)
def create_order(payload: OrderCreate, session: Session = Depends(get_session), idempotency_key: str =Header(...,alias="Idempotency-Key")):
    quantities = normalize_items(payload)
    hash_intention = build_request_hash(quantities=quantities)
    try:
        with session.begin():
            stmt = select(IdempotencyRecord).where(IdempotencyRecord.key == idempotency_key)
            existing_record = session.scalar(stmt)
            if existing_record:
                if existing_record.request_hash == hash_intention:
                    #retry
                    retry_order = session.get(Order, existing_record.order_id)
                    response = OrderResponse(
                        id = retry_order.id,
                        status= retry_order.status,
                        total= retry_order.total,
                        items = retry_order.items
                    )
                    return response
                else:
                    raise HTTPException(409, detail='conflict')
        


            order_items, total = build_order_items(session, quantities)

            order = Order(
                status="PENDIENTE_ACEPTACION",
                total=total,
                items=order_items,
            )
            session.add(order)
            session.flush()
            idempotency_record = IdempotencyRecord(
                key = idempotency_key,
                request_hash = hash_intention,
                order_id = order.id
            )
            session.add(idempotency_record)
            response = OrderResponse.model_validate(order)

    except IntegrityError:
        # The transaction context has rolled back before recovery starts.
        existing_record = session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.key == idempotency_key)
        )
        if existing_record is None:
            raise
        if existing_record.request_hash != hash_intention:
            raise HTTPException(409, detail="conflict")
        order = session.get(Order, existing_record.order_id)
        response = OrderResponse.model_validate(order)

    return response


@app.put("/orders/{order_id}/items", response_model=OrderResponse)
def replace_order_items(
    order_id: int,
    payload: OrderCreate,
    session: Session = Depends(get_session),
):
    with session.begin():
        # Lock before inspecting status; the kitchen UPDATE takes the same row lock.
        order = session.scalar(
            select(Order).where(Order.id == order_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if order is None:
            raise HTTPException(404, detail="El pedido no existe")
        if order.status != "PENDIENTE_ACEPTACION":
            raise HTTPException(409, detail="El pedido ya no permite modificar items")

        items, total = build_order_items(session, normalize_items(payload))
        session.execute(delete(OrderItem).where(OrderItem.order_id == order_id))
        # Explicit deletion is needed: this relationship has no delete-orphan cascade.
        session.expire(order, ["items"])
        order.items = items
        order.total = total
        session.flush()
        response = OrderResponse.model_validate(order)
    return response


@app.patch("/orders/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    session: Session = Depends(get_session),
):
    """Advance kitchen status, or request customer cancellation with CANCELADO.

    Cancellation is allowed only before kitchen acceptance. No authentication
    or actor permissions are enforced by this MVP endpoint.
    """
    expected_status = EXPECTED_STATUS.get(payload.status)
    with session.begin():
        updated = 0
        if expected_status is not None:
            # Check and write in one statement: competing transitions cannot
            # both consume the same predecessor, even if both saw it earlier.
            result = session.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == expected_status)
                .values(status=payload.status)
                .execution_options(synchronize_session=False)
            )
            updated = result.rowcount

        # Read fresh database state after the attempt, including on a lost race.
        order = session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
            .execution_options(populate_existing=True)
        )
        if order is None:
            raise HTTPException(404, detail="El pedido no existe")
        if updated == 0:
            raise HTTPException(
                409,
                detail=f"Transicion no permitida: {order.status} -> {payload.status}",
            )
        response = OrderResponse.model_validate(order)
    return response
