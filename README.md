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
