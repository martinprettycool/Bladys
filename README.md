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


## Personal de desarrollo para smoke tests

No hay registro publico. El comando explicito `python -m bladys.seed_staff`
crea solo `reception_test` (RECEPTION) y `kitchen_test` (KITCHEN), activos,
con `User` y el hash Argon2 existentes. Nunca se ejecuta al arrancar FastAPI.
No crea tablas ni aplica migraciones. Repetirlo conserva IDs, hashes, roles y
actividad de cuentas existentes; una cuenta con rol distinto o inactiva provoca
error y rollback de toda la siembra. No sirve para restablecer contrasenas.

Configuracion obligatoria para el comando:

- `DATABASE_URL`: la misma configuracion que usa el backend, PostgreSQL local.
- `APP_ENV=development`.
- `BLADYS_ALLOW_DEV_STAFF_SEED=1`: habilitacion explicita de esta operacion.
- `DEV_RECEPTION_PASSWORD`: contrasena elegida para `reception_test`.
- `DEV_KITCHEN_PASSWORD`: contrasena elegida para `kitchen_test`.

El destino debe usar `localhost`, `127.0.0.1` o `::1`. Se permite el nombre exacto
`bladys` para la base local de desarrollo, o un nombre con segmento `dev` o `test`
separado por `_`, como `bladys_dev` o `bladys_test`. `APP_ENV=development` y la
habilitacion explicita siguen siendo obligatorios, incluso para `bladys`.
Usa `bladys` para el smoke test y reserva `bladys_test` para pruebas automatizadas:
sus fixtures limpian tablas y pueden borrar las cuentas. Apunta FastAPI y el
comando a la misma base local. No uses datos reales.
Solo se admite el parametro URL opcional `options=-csearch_path=<esquema>`;
parametros que puedan redirigir la conexion se rechazan. Retira tambien
`PGHOST`, `PGHOSTADDR`, `PGSERVICE` y `PGSERVICEFILE` del entorno si estan definidos.
Estas barreras previenen errores comunes; no certifican que una base con nombre
de desarrollo carezca de datos productivos ni detectan tuneles locales hacia produccion.

Desde la raiz del repositorio, con `DATABASE_URL` ya configurada en el entorno
o en `.env` local (ignorado por Git) y apuntando a esa base de desarrollo:
Puedes abrir una terminal PowerShell nueva para mantener los servidores activos.
Las variables `$env:` de otra terminal no se transfieren a ella: vuelve a
configurarlas alli, o conserva `DATABASE_URL` en `.env`. Ejecuta todos los pasos
del bloque siguiente en la misma terminal. Las claves se escriben cuando
Read-Host las solicite; el texto entre comillas es solo la etiqueta del mensaje.

```powershell
# Aplicar las migraciones existentes sobre la base de desarrollo preparada.
uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron aplicar las migraciones.' }
$env:APP_ENV = 'development'
$env:BLADYS_ALLOW_DEV_STAFF_SEED = '1'
try {
    $env:DEV_RECEPTION_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Clave para reception_test' -AsSecureString)).Password
    $env:DEV_KITCHEN_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Clave para kitchen_test' -AsSecureString)).Password
    uv run python -m bladys.seed_staff
    if ($LASTEXITCODE -ne 0) { throw 'La siembra no se completo.' }
} finally {
    Remove-Item Env:DEV_RECEPTION_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:DEV_KITCHEN_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:BLADYS_ALLOW_DEV_STAFF_SEED -ErrorAction SilentlyContinue
}
```

Elegir claves propias diferentes, sin escribirlas literalmente en comandos ni en
archivos versionados. Read-Host oculta la entrada y evita que la clave se escriba
en el historial de comandos. Las variables de entorno contienen texto plano
mientras corre el proceso; no son una boveda de secretos. El script no imprime
contrasenas, hashes ni tokens. Para automatizacion se pueden cargar las mismas
variables desde configuracion local no versionada; `.env` y `.env.*` ya se ignoran.

Para el smoke test, configura tambien `JWT_SECRET_KEY` con el secreto existente
del backend (el seed no emite JWT ni necesita este secreto). Inicia FastAPI y el
proxy del frontend segun `frontend/README.md`, usando la misma `DATABASE_URL`.
Abre `http://127.0.0.1:5173/#reception`, ingresa `reception_test` y la clave elegida:
la cola real debe cargar. Un acceso con `kitchen_test` debe mostrar falta de permiso.
La pantalla de Cocina sigue siendo una vista previa; su API se puede comprobar
sin imprimir el JWT mediante este ejemplo opcional:

```powershell
$smokeCredential = Get-Credential -UserName 'kitchen_test' -Message 'Clave de desarrollo'
try {
    $smokeBody = @{ username = $smokeCredential.UserName; password = $smokeCredential.GetNetworkCredential().Password } | ConvertTo-Json
    $smokeLogin = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/login' -ContentType 'application/json' -Body $smokeBody
    $smokeOrders = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/kitchen/orders/pending' -Headers @{ Authorization = "Bearer $($smokeLogin.access_token)" }
    Write-Host 'Login de Cocina y consulta autorizada completados.'
} finally {
    Remove-Variable smokeCredential, smokeBody, smokeLogin, smokeOrders -ErrorAction SilentlyContinue
}
```

`POST /login` sigue aceptando JSON `{username,password}` y responde
`{access_token,token_type:"bearer"}`; claves incorrectas reciben 401. Las rutas
staff siguen comprobando el rol actual en la base. No se cambiaron autenticacion
de invitados, reglas de pagos ni frontend.

Validacion focalizada con PostgreSQL en un esquema desechable (requiere
`TEST_DATABASE_URL`, no modifica las tablas habituales):

```powershell
uv run python tests/run_order_modification_audit.py tests/test_seed_staff.py tests/test_login.py tests/test_authentication.py tests/test_staff_authorization.py tests/test_current_user.py
```


## Aislamiento de tokens y validacion de arranque

Los JWT de personal requieren `purpose=staff_access`; los de pedidos requieren
`purpose=order_access`. Ningun tipo sirve para autenticar al otro, aunque coincidan
los IDs. Se conservan la verificacion del rol/actividad y la propiedad del pedido.
Los tokens staff anteriores sin `purpose` dejan de ser validos: volver a iniciar
sesion. Los tokens guest existentes conservan validez hasta expirar si no cambia
su clave. Desplegar todos los procesos con la misma version para evitar rechazos
intermitentes durante la actualizacion.

El arranque exige `JWT_SECRET_KEY` y `ORDER_ACCESS_SECRET_KEY` diferentes, cada una
con al menos 32 bytes UTF-8, ocho caracteres distintos y sin espacios exteriores.
Estos controles detectan debilidades evidentes; no certifican entropia. Generar
ambas claves independientemente con un generador criptografico y mantenerlas
fuera del repositorio. Tambien se validan las vigencias positivas existentes.
El servidor debe ejecutar el lifespan ASGI; no deshabilitarlo.

Las pruebas pytest y los servidores de navegador deshabilitan la carga de `.env`
y utilizan claves ficticias. Para pruebas PostgreSQL, proporcionar explicitamente
`TEST_DATABASE_URL` hacia una base desechable antes de ejecutarlas; las fixtures
limpian tablas. La configuracion local `.env` no se carga desde `conftest.py`.
