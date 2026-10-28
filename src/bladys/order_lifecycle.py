from enum import StrEnum


class OrderStatus(StrEnum):
    PENDIENTE_ACEPTACION = "PENDIENTE_ACEPTACION"
    PREPARANDO = "PREPARANDO"
    LISTO = "LISTO"
    ENVIADO = "ENVIADO"
    RECHAZADO = "RECHAZADO"
    CANCELADO = "CANCELADO"


# Every permitted target has exactly one predecessor in this MVP.
EXPECTED_STATUS = {
    OrderStatus.PREPARANDO: OrderStatus.PENDIENTE_ACEPTACION,
    OrderStatus.RECHAZADO: OrderStatus.PENDIENTE_ACEPTACION,
    OrderStatus.CANCELADO: OrderStatus.PENDIENTE_ACEPTACION,
    OrderStatus.LISTO: OrderStatus.PREPARANDO,
    OrderStatus.ENVIADO: OrderStatus.LISTO,
}
