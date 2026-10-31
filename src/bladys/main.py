from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Header, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError

from bladys.payment_proofs import (
    read_and_validate_payment_proof, save_payment_proof, delete_payment_proof,
    resolve_payment_proof,
)
from bladys.authentication import authenticate_user
from bladys.tokens import create_access_token
from bladys.order_tokens import create_order_access_token
from bladys.database import get_session
from bladys.dependencies import get_owned_order, require_role
from bladys.models import Payment, Order, OrderItem, Product, ProductVariant, IdempotencyRecord, UserRole
from bladys.schemas import (
    LoginRequest, TokenResponse, KitchenOrderStatusUpdate,
    PaymentDecision, PaymentResponse,
    OrderCreate, OrderCreateResponse, OrderResponse, OrderStatusUpdate, ProductResponse,
)
from bladys.order_lifecycle import EXPECTED_STATUS, OrderStatus
import hashlib
import json
from bladys.idempotency import build_request_hash

app = FastAPI()


@app.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_session)):
    user = authenticate_user(session, payload.username, payload.password)
    if user is None:
        raise HTTPException(401, detail="Invalid username or password")
    return TokenResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
    )


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


def build_order_create_response(order: Order) -> OrderCreateResponse:
    return OrderCreateResponse(
        **OrderResponse.model_validate(order).model_dump(),
        order_access_token=create_order_access_token(order.id),
    )


@app.post("/orders", response_model=OrderCreateResponse, status_code=201)
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
                    response = build_order_create_response(retry_order)
                    return response
                else:
                    raise HTTPException(409, detail='conflict')
        


            order_items, total = build_order_items(session, quantities)

            order = Order(
                status="PENDIENTE_ACEPTACION",
                total=total,
                items=order_items,
                payment=Payment(),
            )
            session.add(order)
            session.flush()
            idempotency_record = IdempotencyRecord(
                key = idempotency_key,
                request_hash = hash_intention,
                order_id = order.id
            )
            session.add(idempotency_record)
            response = build_order_create_response(order)

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
        response = build_order_create_response(order)

    return response


@app.put(
    "/orders/{order_id}/items", response_model=OrderResponse,
    dependencies=[Depends(get_owned_order)],
)
def replace_order_items(
    order_id: int,
    payload: OrderCreate,
    session: Session = Depends(get_session),
):
    # Authorization has already started the shared session transaction.
    try:
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
        session.commit()
    except Exception:
        session.rollback()
        raise
    return response


def transition_order_status(session: Session, order_id: int, target: OrderStatus):
    """Apply lifecycle rules inside the caller's transaction."""
    expected_status = EXPECTED_STATUS.get(target)
    updated = 0
    if expected_status is not None:
        # Check and write in one statement: competing transitions cannot
        # both consume the same predecessor, even if both saw it earlier.
        conditions = [Order.id == order_id, Order.status == expected_status]
        if target == "PREPARANDO":
            conditions.append(select(Payment.id).where(
                Payment.order_id == Order.id, Payment.status == "APROBADO",
            ).exists())
        result = session.execute(
            update(Order)
            .where(*conditions)
            .values(status=target)
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
            detail=f"Transicion no permitida: {order.status} -> {target}",
        )
    response = OrderResponse.model_validate(order)
    return response


@app.patch(
    "/orders/{order_id}/status", response_model=OrderResponse,
    dependencies=[Depends(get_owned_order)],
)
def update_order_status(
    order_id: int, payload: OrderStatusUpdate,
    session: Session = Depends(get_session),
):
    """Authorize ownership, then atomically cancel only a pending order."""
    # Ownership lookup has already started the shared session transaction.
    try:
        response = transition_order_status(session, order_id, payload.status)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return response


@app.patch(
    "/kitchen/orders/{order_id}/status", response_model=OrderResponse,
    dependencies=[Depends(require_role(UserRole.KITCHEN))],
)
def update_kitchen_order_status(
    order_id: int, payload: KitchenOrderStatusUpdate,
    session: Session = Depends(get_session),
):
    try:
        response = transition_order_status(session, order_id, payload.status)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return response


@app.get(
    "/kitchen/orders/pending", response_model=list[OrderResponse],
    dependencies=[Depends(require_role(UserRole.KITCHEN))],
)
def kitchen_pending_orders(session: Session = Depends(get_session)):
    return session.scalars(
        select(Order).join(Payment).where(
            Order.status == "PENDIENTE_ACEPTACION", Payment.status == "APROBADO",
        ).options(selectinload(Order.items)).order_by(Order.id)
    ).all()


@app.post(
    "/orders/{order_id}/payment/proof", response_model=PaymentResponse,
    dependencies=[Depends(get_owned_order)],
)
async def submit_payment_proof(
    order_id: int, file: UploadFile,
    session: Session = Depends(get_session),
):
    image_bytes = await read_and_validate_payment_proof(file)
    proof_reference = save_payment_proof(image_bytes, file.content_type)
    # Ownership lookup has already started the shared session transaction.
    try:
        result = session.execute(
            update(Payment).where(
                Payment.order_id == order_id, Payment.status == "PENDIENTE_COMPROBANTE",
            ).values(status="PENDIENTE_VERIFICACION", proof_reference=proof_reference)
            .execution_options(synchronize_session=False)
        )
        payment = session.scalar(select(Payment).where(Payment.order_id == order_id)
                                 .execution_options(populate_existing=True))
        if payment is None:
            raise HTTPException(404, detail="El pago no existe")
        if result.rowcount != 1:
            raise HTTPException(409, detail="El pago no permite enviar comprobante")
        response = PaymentResponse.model_validate(payment)
        session.commit()
    except Exception:
        try:
            session.rollback()
        finally:
            try:
                delete_payment_proof(proof_reference)
            except OSError:
                pass  # Best effort: preserve the database failure if deletion fails.
        raise
    return response


@app.get(
    "/payments/pending", response_model=list[PaymentResponse],
    dependencies=[Depends(require_role(UserRole.RECEPTION))],
)
def payments_pending_verification(session: Session = Depends(get_session)):
    return session.scalars(select(Payment).where(
        Payment.status == "PENDIENTE_VERIFICACION",
    ).order_by(Payment.id)).all()


@app.get(
    "/payments/{payment_id}/proof", response_class=FileResponse,
    dependencies=[Depends(require_role(UserRole.RECEPTION))],
)
def get_payment_proof(payment_id: int, session: Session = Depends(get_session)):
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(404, detail="El pago no existe")
    if not payment.proof_reference:
        raise HTTPException(404, detail="El comprobante no existe")
    try:
        path = resolve_payment_proof(payment.proof_reference)
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(404, detail="El comprobante no existe") from None
    media_type = {".jpg": "image/jpeg", ".png": "image/png"}[path.suffix]
    return FileResponse(path, media_type=media_type)


@app.patch(
    "/payments/{payment_id}/status", response_model=PaymentResponse,
    dependencies=[Depends(require_role(UserRole.RECEPTION))],
)
def decide_payment(
    payment_id: int, payload: PaymentDecision,
    session: Session = Depends(get_session),
):
    try:
        result = session.execute(
            update(Payment).where(
                Payment.id == payment_id, Payment.status == "PENDIENTE_VERIFICACION",
            ).values(status=payload.status).execution_options(synchronize_session=False)
        )
        payment = session.scalar(select(Payment).where(Payment.id == payment_id)
                                 .execution_options(populate_existing=True))
        if payment is None:
            raise HTTPException(404, detail="El pago no existe")
        if result.rowcount != 1:
            raise HTTPException(409, detail="El pago no permite esta decision")
        if payload.status == "RECHAZADO":
            session.execute(update(Order).where(Order.id == payment.order_id)
                            .values(status="CANCELADO").execution_options(synchronize_session=False))
        response = PaymentResponse.model_validate(payment)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return response
