# Blady's Pizza

Challenge 1A: solo modelo de datos, sin endpoints.

## Preparar
```powershell
uv sync
$env:DATABASE_URL = "postgresql+psycopg://usuario:password@localhost:5432/bladys"
uv run alembic upgrade head
```

Usar una base PostgreSQL nueva y vacia. La revision inicial local sustituye
la implementacion anterior; no aplicar sobre una base con la antigua revision 0001.
No se ha modificado ninguna base existente.

Modelos: src/bladys/models.py. Migracion: alembic/versions/0001_initial.py.
Importes: Decimal / Numeric(12, 2), hasta 9999999999.99, dos decimales.
Solo description admite NULL. Estado inicial: PENDIENTE_ACEPTACION.
Las FK RESTRICT preservan referencias al borrar productos o pedidos.
is_available=false retira un producto del menu sin borrar su historial.
unit_price almacena el precio historico; copiarlo y calcular total queda para otro paso.
Los defaults se aplican al insertar en la base.

## Verificar
```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://usuario:password@localhost:5432/bladys_test"
uv run pytest -q
uv run alembic upgrade head --sql
```

Los tests requieren PostgreSQL y permisos para crear un schema temporal.
Sin TEST_DATABASE_URL se omiten; eso no verifica integridad en PostgreSQL.
El downgrade elimina las tres tablas y sus datos.


## Transfer payments MVP

`POST /orders` creates one Payment in the same transaction, including idempotency
recovery. Payments have a unique, required `order_id` with restricted deletion.
Creation through the application guarantees a Payment per Order; the foreign key
and uniqueness enforce ownership and at most one Payment, not existence for raw SQL
Order inserts.

- `POST /orders/{order_id}/payment/proof`: `{"proof_reference": "opaque-reference"}`.
- `GET /payments/pending`: reception verification queue.
- `PATCH /payments/{payment_id}/status`: `{"status": "APROBADO"}` or `RECHAZADO`.
- `GET /kitchen/orders/pending`: only pending Orders with approved Payments.

The state machine is PENDIENTE_COMPROBANTE -> PENDIENTE_VERIFICACION ->
APROBADO or RECHAZADO. Decisions and proof submission use conditional updates;
rejected Payments are terminal. Rejection cancels the Order in the same transaction.
Customer cancellation preserves an approved Payment for manual refund handling.
Existing MVP endpoints do not enforce actor authentication.

Preparation uses a conditional Order UPDATE with an EXISTS subquery requiring an
approved Payment. PostgreSQL cannot see another transaction's uncommitted approval.
Approval is terminal, so a successful preparation cannot outlive/reverse approval
through these operations. Direct SQL writes are outside lifecycle enforcement.

Apply migration `0005` with Alembic before running the new application. It backfills
pending Payments for existing Orders and refuses advanced legacy Orders
(PREPARANDO/LISTO/ENVIADO); resolve their payment-history policy explicitly before
migration rather than inventing approvals. No migration is run on startup.


## Guest Order access-token issuance

Set `ORDER_ACCESS_SECRET_KEY` in the environment (or local `.env`) to a strong
secret distinct from the staff `JWT_SECRET_KEY`. There is no fallback to the staff
secret. `ORDER_ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 1440 (24 hours) and must be
a positive integer.

`POST /orders` returns the existing Order fields plus `order_access_token`.
The signed capability contains `sub` (Order ID as a string), `purpose` set to
`order_access`, `iat`, and `exp`. It represents possession of authority over one
Order, not a staff identity. Tokens are not persisted. Same-key, same-intent retries
return the same Order with a newly issued token; token strings may differ.
This step only issues tokens; existing Order endpoints do not enforce ownership yet.


## Menú real MVP

Aplicar explícitamente la migración antes de usar el backend actualizado:

```powershell
uv run alembic upgrade head
uv run python -m bladys.seed_menu
```

El seed converge los 11 productos y 47 variantes del menú en una transacción.
Actualiza nombres conocidos, categorías, descripciones, precios y límites;
conserva IDs y disponibilidad existente. Reconoce los nombres del seed anterior.
No elimina filas ajenas ni variantes históricas. Nombres duplicados ambiguos
provocan error y rollback; no se adivina qué registro usar. Ejecutarlo de forma
serial como tarea explícita, nunca al iniciar FastAPI.

GET /products conserva el filtro de disponibilidad y añade category,
is_available, flavor_options y description/max_flavors/is_available por variante.
POST /orders y PUT /orders/{id}/items aceptan selected_flavors (lista opcional).
Para ALITAS se requieren entre 1 y max_flavors sabores canónicos sin duplicados;
para las demás categorías la lista debe estar vacía. Se guarda una instantánea
JSON por item, visible también en GET /orders/{id} (con token de propiedad) y cocina.
El orden de sabores no cambia la identidad ni el hash; combinaciones distintas
permanecen en líneas separadas. Los hashes sin sabores mantienen el formato previo.
La migración deja categorías desconocidas en NULL y selecciones históricas vacías;
no inventa salsas compradas ni modifica pedidos, pagos o claves existentes.


## Versioned Order modification and proof submission

Apply migration 0008 explicitly BEFORE starting this backend version:

```powershell
uv run alembic upgrade head
```

No migration runs on startup. Migration 0008 adds orders.version as a non-null
positive integer, backfills existing Orders to 1 and retains a server default of 1.
The version identifies the item/price snapshot. Only successful item replacements
increment it, exactly once; failures, proof uploads and status transitions do not.

GET /orders/{id} requires X-Order-Access-Token and returns id, version, status,
total and items. Items, total and version are read in one database statement.
POST /orders creation and its idempotent recovery also include the current version.
The same guest token continues authorizing the same Order after replacement.

PUT /orders/{id}/items requires X-Order-Access-Token and JSON:

```json
{
  "expected_version": 1,
  "items": [{"variant_id": 123, "quantity": 2, "selected_flavors": []}]
}
```

expected_version must be a positive JSON integer (not boolean/string). Prices and
totals are not accepted. After Order then Payment row locks, replacement requires
PENDIENTE_ACEPTACION, an existing PENDIENTE_COMPROBANTE Payment, NULL proof_reference,
and a matching version. The same Order/Payment IDs survive. Prices are read from
the catalog; items, total and incremented version commit atomically.

POST /orders/{id}/payment/proof now requires multipart fields file and
expected_version, plus X-Order-Access-Token. The payment screen must first GET the
Order, show its authoritative total and submit that reviewed version. File
validation/writing happens before row locks. Under Order -> Payment locks, the
same unpaid-state conditions and version are checked. A stale upload returns 409
and removes its new file without recording a payment. A committed proof prevents
all subsequent item replacement. Existing rejection/cancellation behavior remains.

Conflicts return HTTP 409 with detail.code:

- ORDER_NOT_EDITABLE: Order status is incompatible.
- PAYMENT_MISSING: no associated Payment; fail closed.
- PAYMENT_NOT_EDITABLE: Payment advanced or proof_reference is non-NULL.
- ORDER_VERSION_CONFLICT: expected_version is stale; detail.current_version
  identifies the observed version, but clients must GET and review all current data.

Missing/invalid versions return 422; guest authorization remains 401/403 as before.
This is a breaking precondition for PUT/proof callers; there is no optional fallback.

On lost edit responses, GET the Order to reconcile version, items and total.
Never automatically substitute a newer expected_version into an old request.
A repeated old PUT cannot overwrite a later successful modification. There is no
new PUT idempotency ledger or automatic edit replay infrastructure.

Edit, proof and reception decisions all lock Order before Payment. Reception's
initial Payment-to-Order-ID lookup is non-locking; identity/state are rechecked
under locks. Rejection updates the already locked Order in the same transaction.
Cancellation/kitchen transitions update only Order; they acquire that row's lock
and never introduce Payment -> Order row locking. Kitchen still requires a
committed approved Payment through its existing conditional UPDATE.

Regression suite (the runner confines existing TRUNCATE fixtures to a disposable
PostgreSQL schema, then drops it):

```powershell
.venv/Scripts/python.exe tests/run_order_modification_audit.py tests
```

PostgreSQL tests force and observe lock contention, exercise edit/proof in both
orders, reception approval/rejection versus edit, stale simultaneous clients,
late replay, rollback, coherent reads, proof cleanup and real Alembic backfill.
Application lifecycle guarantees do not cover direct SQL mutations. Proof files
and database rows are not one atomic resource: compensating cleanup is best effort
and a process/filesystem failure may leave an orphan file. No frontend editing or
proof UI is implemented by this backend change.
