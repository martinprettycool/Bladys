from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, func, true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


from bladys.menu import ProductCategory, WING_FLAVORS


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("category IN ('PIZZA', 'HAMBURGUESA', 'ALITAS', 'PAPITAS')", name="ck_products_category"),
    )

    category: Mapped[str | None] = mapped_column(String(20), nullable=True)

    @property
    def flavor_options(self) -> list[str]:
        return list(WING_FLAVORS) if self.category == ProductCategory.ALITAS else []


    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(),
    )

    variants: Mapped[list["ProductVariant"]] = relationship(
        back_populates="product", passive_deletes="all",
    )


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_product_variants_price_nonnegative"),
        CheckConstraint("max_flavors > 0", name="ck_product_variants_max_flavors_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_flavors: Mapped[int | None] = mapped_column(nullable=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(),
    )

    product: Mapped[Product] = relationship(back_populates="variants")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="variant", passive_deletes="all",
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total >= 0", name="ck_orders_total_nonnegative"),
        CheckConstraint("version > 0", name="ck_orders_version_positive"),
        CheckConstraint(
            "status IN ('PENDIENTE_ACEPTACION', 'PREPARANDO', 'LISTO', "
            "'ENVIADO', 'RECHAZADO', 'CANCELADO')",
            name="ck_orders_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="PENDIENTE_ACEPTACION",
    )
    version: Mapped[int] = mapped_column(nullable=False, server_default="1")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", passive_deletes="all",
    )
    payment: Mapped["Payment | None"] = relationship(
        back_populates="order", passive_deletes="all",
    )
    idempotency_records: Mapped[list["IdempotencyRecord"]] = relationship(
        back_populates="order", passive_deletes="all",
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    order: Mapped[Order] = relationship(back_populates="idempotency_records")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_price_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False,
    )
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=False,
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    selected_flavors: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]",
    )

    order: Mapped[Order] = relationship(back_populates="items")
    variant: Mapped[ProductVariant] = relationship(back_populates="items")

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDIENTE_COMPROBANTE', 'PENDIENTE_VERIFICACION', 'APROBADO', 'RECHAZADO')",
            name="ck_payments_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="PENDIENTE_COMPROBANTE",
    )
    proof_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    order: Mapped[Order] = relationship(back_populates="payment")


class UserRole(StrEnum):
    KITCHEN = "KITCHEN"
    RECEPTION = "RECEPTION"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('KITCHEN', 'RECEPTION')", name="ck_users_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    role: Mapped[UserRole] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False,
    )
