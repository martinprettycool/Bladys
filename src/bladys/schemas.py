from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductVariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    variants: list[ProductVariantResponse]


class OrderItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: int
    quantity: int = Field(gt=0, strict=True)


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemCreate] = Field(min_length=1)


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    variant_id: int
    quantity: int
    unit_price: Decimal


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total: Decimal
    items: list[OrderItemResponse]
