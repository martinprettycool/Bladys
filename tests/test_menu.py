from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text

from bladys.menu import WING_FLAVORS
from bladys.models import Product, ProductVariant, OrderItem, Payment
from bladys.seed_menu import seed_menu


@pytest.fixture
def menu(session):
    seed_menu(session)
    session.commit()
    return {p.name: p for p in session.scalars(select(Product)).all()}


def test_seed_complete_menu_and_convergence(session, menu):
    """Contexto: se carga y vuelve a sincronizar el menú operativo.
    Garantía: no duplica filas ni reactiva productos o variantes retirados.
    Escenarios: primera carga, cambio de precio/disponibilidad y segunda carga.
    """
    original_ids = {p.name: (p.id, [v.id for v in p.variants]) for p in menu.values()}
    menu["Alitas"].is_available = False
    menu["Alitas"].variants[0].is_available = False
    menu["Alitas"].variants[0].price = Decimal("99.00")
    session.commit()
    seed_menu(session)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Product)) == 11
    assert session.scalar(select(func.count()).select_from(ProductVariant)) == 47
    assert {p.name: (p.id, [v.id for v in p.variants]) for p in menu.values()} == original_ids
    assert not menu["Alitas"].is_available
    assert not menu["Alitas"].variants[0].is_available
    assert menu["Alitas"].variants[0].price == Decimal("2.70")
    assert "Ensalada" not in menu
    pizzas = [p for p in menu.values() if p.category == "PIZZA"]
    assert len(pizzas) == 8
    for pizza in pizzas:
        assert pizza.description
        assert {v.name: v.price for v in pizza.variants} == {
            "Personal": Decimal("3.00"), "Pequeña": Decimal("5.00"),
            "Mediana": Decimal("10.00"), "Familiar": Decimal("15.00")}
        assert all(v.max_flavors is None for v in pizza.variants)
    assert {v.name: (v.price, v.description) for v in menu["Hamburguesas"].variants} == {
        "ORIGINAL": (Decimal("2.50"), "Carne, cheddar, papas y salsa de la casa."),
        "DOBLE O NADA": (Decimal("3.40"), "2 carnes, cheddar, papas, huevo y salsa de la casa."),
        "TOCINETA": (Decimal("3.25"), "Carne, cheddar, tocino, papas y salsa de la casa."),
        "CHORI": (Decimal("2.50"), "Carne, cheddar, chorizo, huevo y papas."),
        "POLLO CLÁSICO": (Decimal("2.00"), "Pechuga, vegetales, cheddar y papas."),
        "BBQ": (Decimal("3.00"), "Pechuga, vegetales, tocino, mozzarella, papas y salsa BBQ.")}
    assert {v.name: (v.price, v.max_flavors) for v in menu["Alitas"].variants} == {
        "INDIVIDUAL": (Decimal("2.70"), 1), "DOBLE": (Decimal("5.00"), 1),
        "GRANDE": (Decimal("7.50"), 2), "FAMILIAR": (Decimal("12.50"), 2),
        "COMELONES": (Decimal("16.00"), 3)}
    assert {v.name: v.price for v in menu["Papitas"].variants} == {
        "Papas + Salchicha": Decimal("1.50"), "Papas + Pollo": Decimal("2.50"),
        "Papas + Salchicha + Huevo": Decimal("1.75"),
        "Papas + Cheddar + Tocino + Huevo": Decimal("2.75")}
    assert all(v.max_flavors is None for name in ("Papitas", "Hamburguesas") for v in menu[name].variants)


def test_catalog_metadata(client, menu):
    products = client.get("/products").json()
    assert len(products) == 11
    for product in products:
        assert product["category"] in {"PIZZA", "HAMBURGUESA", "ALITAS", "PAPITAS"}
        assert product["is_available"] is True
        assert product["flavor_options"] == (list(WING_FLAVORS) if product["category"] == "ALITAS" else [])
        for variant in product["variants"]:
            assert {"id", "name", "price", "description", "is_available", "max_flavors"} <= variant.keys()


@pytest.mark.parametrize("name,flavors,status", [
    ("INDIVIDUAL", ["BBQ"], 201), ("INDIVIDUAL", ["BBQ", "Buffalo"], 422),
    ("DOBLE", ["BBQ", "Buffalo"], 422), ("DOBLE", ["Buffalo"], 201),
    ("GRANDE", ["BBQ", "Buffalo"], 201),
    ("GRANDE", ["BBQ", "Buffalo", "Maracuyá"], 422),
    ("FAMILIAR", ["BBQ", "Buffalo"], 201),
    ("FAMILIAR", ["BBQ", "Buffalo", "Maracuyá"], 422),
    ("COMELONES", ["BBQ", "Buffalo", "Maracuyá"], 201),
    ("COMELONES", ["BBQ", "Buffalo", "Maracuyá", "Ajo parmesano"], 422),
    ("GRANDE", [], 422), ("GRANDE", ["unknown"], 422),
    ("GRANDE", ["BBQ", "BBQ"], 422),
])
def test_flavor_limits_post_and_put(client, menu, name, flavors, status):
    variant = next(v for v in menu["Alitas"].variants if v.name == name)
    item = {"variant_id": variant.id, "quantity": 1, "selected_flavors": flavors}
    result = client.post("/orders", headers={"Idempotency-Key": "flavor-check"}, json={"items": [item]})
    assert result.status_code == status
    base = client.post("/orders", headers={"Idempotency-Key": "base"}, json={"items": [
        {"variant_id": menu["Papitas"].variants[0].id, "quantity": 1}]}).json()
    headers = {"X-Order-Access-Token": base["order_access_token"]}
    before = client.get(f"/orders/{base['id']}", headers=headers).json()
    replacement = client.put(f"/orders/{base['id']}/items", headers=headers, json={"expected_version": base["version"], "items": [item]})
    assert replacement.status_code == (200 if status == 201 else status)
    fetched = client.get(f"/orders/{base['id']}", headers=headers).json()
    if status == 201:
        assert fetched["items"][0]["selected_flavors"] == sorted(flavors)
    else:
        assert fetched == before


def test_other_categories_reject_flavors(client, menu):
    for name in ("ESPECIAL", "Hamburguesas", "Papitas"):
        assert client.post("/orders", headers={"Idempotency-Key": name}, json={"items": [
            {"variant_id": menu[name].variants[0].id, "quantity": 1, "selected_flavors": ["BBQ"]}
        ]}).status_code == 422


def test_flavor_identity_idempotency_snapshot_and_kitchen(client, session, menu, staff_headers):
    """Contexto: un cliente pide alitas con distintas combinaciones y reintenta.
    Garantía: el orden de salsas no cambia la intención; otra combinación sí.
    Escenarios: líneas equivalentes se suman, distintas se separan, reintento coincide
    o entra en conflicto; reemplazo y cocina conservan la selección histórica.
    """
    variant = next(v for v in menu["Alitas"].variants if v.name == "GRANDE")
    def item(flavors, quantity=1):
        return {"variant_id": variant.id, "quantity": quantity, "selected_flavors": flavors}
    lines = [item(["BBQ", "Buffalo"]), item(["Buffalo", "BBQ"], 2), item(["Maracuyá", "Ajo parmesano"])]
    first = client.post("/orders", headers={"Idempotency-Key": "identity"}, json={"items": lines})
    assert first.status_code == 201
    body = first.json()
    assert body["total"] == "30.00"
    assert len(body["items"]) == 2
    assert body["items"][0]["quantity"] == 3
    retry = client.post("/orders", headers={"Idempotency-Key": "identity"}, json={"items": [
        item(["Ajo parmesano", "Maracuyá"]), item(["Buffalo", "BBQ"], 3)]})
    assert retry.status_code == 201
    assert retry.json()["id"] == body["id"]
    conflict = client.post("/orders", headers={"Idempotency-Key": "identity"}, json={"items": [
        item(["BBQ", "Maracuyá"], 3), item(["Ajo parmesano", "Maracuyá"])]})
    assert conflict.status_code == 409
    auth = {"X-Order-Access-Token": body["order_access_token"]}
    replacement = client.put(f"/orders/{body['id']}/items", headers=auth, json={"expected_version": body["version"], "items": lines})
    assert replacement.status_code == 200
    assert replacement.json()["items"] == body["items"]
    session.expire_all()
    payment = session.scalar(select(Payment).where(Payment.order_id == body["id"]))
    payment.status = "APROBADO"
    variant.max_flavors = 1
    session.commit()
    fetched = client.get(f"/orders/{body['id']}", headers=auth)
    assert fetched.json()["items"] == body["items"]
    kitchen = client.get("/kitchen/orders/pending", headers=staff_headers["KITCHEN"])
    assert kitchen.status_code == 200
    assert kitchen.json()[0]["items"] == body["items"]
    session.expire_all()
    persisted = session.scalars(select(OrderItem).where(OrderItem.order_id == body["id"])).all()
    assert [i.selected_flavors for i in persisted] == [i["selected_flavors"] for i in body["items"]]
    assert client.get(f"/orders/{body['id']}").status_code == 401


def test_menu_migration_preserves_legacy_rows():
    """Contexto: una base anterior contiene un producto y una compra sin salsas.
    Garantía: migrar conserva sus datos sin inventar categoría ni selección.
    Escenarios: se aplica 0007 sobre tablas anteriores y se leen defaults y NULL.
    """
    spec = importlib.util.spec_from_file_location("menu_migration", Path(__file__).parents[1] / "alembic/versions/0007_menu_flavors.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    from sqlalchemy import create_engine
    legacy = create_engine("sqlite://")
    try:
        with legacy.begin() as conn:
            conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
            conn.execute(text("CREATE TABLE product_variants (id INTEGER PRIMARY KEY, price NUMERIC NOT NULL)"))
            conn.execute(text("CREATE TABLE order_items (id INTEGER PRIMARY KEY, quantity INTEGER NOT NULL)"))
            conn.execute(text("INSERT INTO products VALUES (1, 'Legacy')"))
            conn.execute(text("INSERT INTO product_variants VALUES (1, 3)"))
            conn.execute(text("INSERT INTO order_items VALUES (1, 2)"))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert conn.execute(text("SELECT name, category FROM products")).one() == ("Legacy", None)
            assert conn.execute(text("SELECT price, description, max_flavors FROM product_variants")).one() == (3, None, None)
            assert conn.execute(text("SELECT quantity, selected_flavors FROM order_items")).one() == (2, '[]')
    finally:
        legacy.dispose()


def test_seed_reuses_legacy_product_and_preserves_unknown_rows(session):
    legacy = Product(name="Hawayana", is_available=False, variants=[
        ProductVariant(name="Personal", price=Decimal("1.00"), is_available=False)])
    unknown = Product(name="Legacy unknown")
    session.add_all([legacy, unknown])
    session.commit()
    product_id, variant_id = legacy.id, legacy.variants[0].id
    seed_menu(session)
    session.commit()
    assert legacy.name == "HAWAIANA"
    assert legacy.category == "PIZZA"
    assert legacy.id == product_id
    assert legacy.variants[0].id == variant_id
    assert not legacy.is_available and not legacy.variants[0].is_available
    assert unknown.category is None
    assert session.scalar(select(func.count()).select_from(Product)) == 12
    assert session.scalar(select(func.count()).select_from(ProductVariant)) == 47


def test_ambiguous_seed_rolls_back(session):
    """Contexto: datos previos tienen dos productos con el mismo nombre.
    Garantía: el seed no elige un ID arbitrario ni deja cambios parciales.
    Escenarios: un producto anterior se sincroniza antes de detectar ambigüedad.
    """
    session.add_all([Product(name="Alitas"), Product(name="Alitas")])
    session.commit()
    with pytest.raises(ValueError, match="Ambiguous menu product"):
        with session.begin():
            seed_menu(session)
    assert session.scalar(select(func.count()).select_from(Product)) == 2
    assert session.scalar(select(func.count()).select_from(ProductVariant)) == 0


def test_flavor_snapshot_postgres(postgres_client, postgres_session):
    """Contexto: se compra alitas y luego cambia el menú en PostgreSQL.
    Garantía: la selección histórica persiste y es legible por la API.
    Escenarios: compra con dos sabores, cambio del límite y lectura posterior.
    """
    seed_menu(postgres_session)
    postgres_session.commit()
    variant = postgres_session.scalar(select(ProductVariant).join(Product).where(
        Product.category == "ALITAS", ProductVariant.name == "GRANDE"))
    response = postgres_client.post("/orders", headers={"Idempotency-Key": "pg-flavors"}, json={
        "items": [{"variant_id": variant.id, "quantity": 1, "selected_flavors": ["Buffalo", "BBQ"]}]})
    assert response.status_code == 201
    body = response.json()
    variant.max_flavors = 1
    postgres_session.commit()
    postgres_session.expire_all()
    persisted = postgres_session.scalar(select(OrderItem).where(OrderItem.order_id == body["id"]))
    assert persisted.selected_flavors == ["BBQ", "Buffalo"]
    result = postgres_client.get(f"/orders/{body['id']}", headers={
        "X-Order-Access-Token": body["order_access_token"]})
    assert result.status_code == 200
    assert result.json()["items"][0]["selected_flavors"] == ["BBQ", "Buffalo"]
