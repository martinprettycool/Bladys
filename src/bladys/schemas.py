from typing import Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from bladys.menu import ProductCategory
from bladys.order_lifecycle import OrderStatus
from bladys.payment_lifecycle import PaymentStatus


class ProductVariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    description: str | None
    is_available: bool
    max_flavors: int | None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    category: ProductCategory | None
    is_available: bool
    flavor_options: list[str]
    variants: list[ProductVariantResponse]


class OrderItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: int
    quantity: int = Field(gt=0, strict=True)
    selected_flavors: list[str] = Field(default_factory=list)


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemCreate] = Field(min_length=1)


class OrderReplace(OrderCreate):
    expected_version: int = Field(gt=0, strict=True)


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    variant_id: int
    quantity: int
    unit_price: Decimal
    selected_flavors: list[str]


class OrderStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[OrderStatus.CANCELADO]


class KitchenOrderStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[OrderStatus.PREPARANDO, OrderStatus.LISTO, OrderStatus.ENVIADO]


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    status: OrderStatus
    total: Decimal
    items: list[OrderItemResponse]


class OrderCreateResponse(OrderResponse):
    order_access_token: str


class PaymentProofSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proof_reference: str = Field(min_length=1)


class PaymentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[PaymentStatus.APROBADO, PaymentStatus.RECHAZADO]


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    status: PaymentStatus
    proof_reference: str | None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
