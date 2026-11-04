"""Small, explicit menu domain shared by catalog and ordering."""
from enum import StrEnum


class ProductCategory(StrEnum):
    PIZZA = "PIZZA"
    HAMBURGUESA = "HAMBURGUESA"
    ALITAS = "ALITAS"
    PAPITAS = "PAPITAS"


WING_FLAVORS = ("BBQ", "Miel y mostaza", "BBQ picante", "Buffalo", "Ajo parmesano", "Maracuyá")
