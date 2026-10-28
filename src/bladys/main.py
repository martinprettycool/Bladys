from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError

from bladys.database import get_session
from bladys.models import Order, OrderItem, Product, ProductVariant, IdempotencyRecord
from bladys.schemas import OrderCreate, OrderResponse, ProductResponse
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


@app.post("/orders", response_model=OrderResponse, status_code=201)
def create_order(payload: OrderCreate, session: Session = Depends(get_session), idempotency_key: str =Header(...,alias="Idempotency-Key")):
    # Type hint para que tipo de datos va entrar dentro del dict 
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.variant_id] = quantities.get(item.variant_id, 0) + item.quantity
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
