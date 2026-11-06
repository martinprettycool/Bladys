# Blady's customer menu and checkout

Buildless HTML, CSS and native JavaScript modules; no runtime dependencies.
Start FastAPI with the project's configured PostgreSQL database and migrations,
then run from the repository root:

    python frontend/serve.py

Open http://127.0.0.1:5173. Use --backend and --port to override the API origin
and frontend port. Restart this server after updating serve.py. Static files use
Cache-Control: no-store during development to avoid stale JS and CSS.

## API integration

The development server proxies these customer routes (staff routes are listed below):

- GET /products
- POST /orders (forwards Idempotency-Key and JSON body)
- GET /orders/{id} and GET /orders/{id}/payment (forward X-Order-Access-Token)
- PUT /orders/{id}/items (forwards JSON and X-Order-Access-Token)
- POST /orders/{id}/payment/proof (forwards native multipart and X-Order-Access-Token)

HTTP error status and body are preserved. Production hosting must proxy these
routes to FastAPI as well. Optional meta[name="api-base"] can select an API origin
with CORS allowing GET/POST/PUT and Content-Type, Idempotency-Key and X-Order-Access-Token.
No staff Authorization header is used for guest orders.

ORDER_ACCESS_SECRET_KEY must already be configured on FastAPI, separately from
JWT_SECRET_KEY. No secrets are configured or sent by the frontend. A missing
backend secret produces an error, never a simulated success. No migrations or
seeds run on application startup.

## Customer flow

Menu category discovery and search use GET /products. Cart identities include
variant ID and the unordered flavor set. Variant and flavor configuration reuse
the existing dialogs. Carrito -> Continuar a confirmar -> #checkout shows names,
variants, flavors, quantities, display unit prices, subtotals and estimated total.
Customers can return to the cart before confirming.

Confirmar pedido sends only {items:[{variant_id,quantity,selected_flavors}]}.
No client prices or totals are sent. After success, #my-order uses the backend's
ID, status, item quantities, unit prices, flavors and total. Names are labels from
the submitted cart because OrderResponse contains variant IDs without names.
The successful selection is cleared; changes made during an in-flight request
are retained. Mi pedido is available in the header after confirmed creation.
Reloading or pressing Actualizar estado retrieves the real order with its guest
token. Failed refresh retains the last confirmed receipt with an explicit notice.

Verified bank information is not configured in the current frontend/backend
contract. The confirmation explains that instructions still need configuration
and directs customers to the restaurant for verified details. It does not claim
payment or approval. The Order detail now supports proof upload; reception still decides approval.

## Retry and guest persistence

src/checkout-state.js stores one JSON record under sessionStorage key
bladys.guest-checkout.v1: current cart, pending attempt and latest confirmed
receipt. The attempt contains a cryptographically generated UUID, exact payload
and display labels, written BEFORE POST. Blocked storage blocks submission.
After server success, ID and token are saved BEFORE cart cleanup. Both writes
are checked: credential recording retains the snapshot/cart, then cleanup is
persisted before mutating memory. If either write fails, cart and confirmed
credentials remain in memory, with an explicit warning that the Order already
exists. Guardar acceso al pedido retries local persistence without POST. A saved
confirmed attempt also recovers locally after reload, without another POST.
If no confirmed receipt could be saved before reload, the original saved key and
payload recover the same backend Order through an idempotent POST.

A synchronous busy flag and disabled CTA prevent duplicate in-flight requests.
Timeout (15 seconds), HTTP 408 (including non-JSON bodies), network failure,
invalid success response and server 5xx
are uncertain outcomes. They never clear the cart or claim success. Retry sends
the same saved key and exact request, including after reload. Backend idempotency
is responsible for returning the existing Order. HTTP 400/404/409/422 errors are
visible and preserve the cart; a 409 never triggers an automatic new key.
Submitted payload, key and labels are immutable and independent of the cart.
Attempt status is persisted as submitting, uncertain, rejected or confirmed
(ready means no request was sent). A restored submitting attempt becomes uncertain.
Edits, navigation and reload never discard an unresolved attempt, even when the
cart is empty or different. Checkout displays the submitted snapshot and explains
that later cart edits remain separate. Retry resolves that attempt first using
its original key and payload. A new purchase may begin after recovery and gets a
new UUID. Definitively rejected/unsent attempts may be replaced by edited carts.
There is no discard action for uncertain orders: contact the restaurant if the
original attempt cannot be resolved. This deliberately prevents ambiguous overlap.

The capability token is separate from the visible order data and is sent only
in X-Order-Access-Token. No tokens in URLs, rendered text, console logs, analytics
or staff JWT storage. Every confirmed guest order is retained in receipts, indexed by its own ID and capability; the legacy receipt field identifies the latest purchase. Existing single-receipt sessions migrate on load.

Security and lifetime trade-offs:

- sessionStorage survives reload and navigation within the same origin/tab.
  It normally ends when that tab closes, unlike localStorage. Browser session
  restoration and duplicated tabs may retain/copy it; it is not a revocation rule.
- Separate fresh tabs/browsers/devices do not provide shared cart/order recovery.
  Lost or expired capability access requires contacting the restaurant; no staff
  token fallback or automatic resubmission creates a replacement Order.
- Same-origin scripts (including XSS) and browser developer tools can read it.
  This is a scoped guest MVP, not HttpOnly cookie protection. Use HTTPS in
  production, trusted scripts and appropriate CSP; server token expiry remains
  authoritative (backend default: 24 hours).
- crypto.randomUUID requires a secure context: HTTPS or localhost/127.0.0.1.
  Client storage is untrusted; PostgreSQL and FastAPI enforce prices and rules.

## Structure

- src/api.js: catalog, timed POST and token-authenticated GET; response validation.
- src/cart.js: integer cents for display and flavor-aware cart identity.
- src/menu.js: catalog grouping/search, cart dialog and checkout entry.
- src/checkout-state.js: attempt identity, guest persistence and submission state.
- src/checkout.js: review and real order confirmation screens.
- src/app.js: hash routing/header; reception/kitchen operational screens and foundation preview.
- src/configurator.js: existing variant/flavor dialogs.
- src/category-card.js: centralized API category to local illustration mapping.
- styles/app.css: existing design tokens/layout with scoped checkout additions.
- serve.py: development static server and customer API proxy.

Mobile uses a compact 2-column category grid, 4 columns at 768px. Supplied AI
illustrations in public/menu use responsive 400/800px JPEG derivatives with
lazy loading and a text fallback. Product rows avoid repetitive photos. Customer
routes include #menu, #category-pizza, #category-hamburguesa, #category-alitas,
#category-papitas, #checkout and #my-order; #pizzas remains an alias. Staff routes
#reception and #kitchen open staff login; #foundation remains a preview shell.

## Validation

    node --test frontend/tests/cart.test.mjs frontend/tests/checkout.test.mjs frontend/tests/checkout-audit.test.mjs
    node frontend/tests/browser.mjs

There is no bundler, package installation, lint configuration or build command.
The browser review uses .venv, installed Chrome, an isolated seeded in-memory
SQLite database, and ports 8174/5179/9234. No restaurant database is accessed.
It checks 360/390/430/768/1440px, configuration, search, cart, focus, API states,
real checkout, a rejected request, lost successful response, same-key recovery
after reload, confirmed backend totals/ID, token use and persisted flavors.
Screenshots are saved under frontend/review; tokens are never in screenshots.

For the same end-to-end flow against PostgreSQL:

    .venv/Scripts/python.exe frontend/tests/browser_postgres.py

Requires TEST_DATABASE_URL and create/drop schema permissions. This wrapper creates
a unique bladys_browser_* schema, seeds only that isolated schema, runs Chrome
against the real FastAPI backend, and checks exactly one Order, Payment and
idempotency record plus both flavor selections. The schema is dropped in finally.
No migrations or schema changes are applied to restaurant data. Test-only failure
injection lives in the browser harness, never in application code. The PostgreSQL
check covers persistence and retry through checkout, not all concurrency orderings,
other browsers or production deployment. Use --orders --proof for the upload workflow below.

## Mis pedidos y modificación segura

`#orders` lista los accesos guardados en esta pestaña; `#my-order` abre la última compra.
No hay cuenta de cliente ni descubrimiento de pedidos sin token. Se mantiene sessionStorage
(`bladys.guest-checkout.v1`): recargar conserva todos los pedidos; cerrar la pestaña,
terminar navegación privada o expirar el token puede perder el acceso. La restauración
real de pestañas depende del navegador. No se comparten accesos entre dispositivos.
No se guardan tokens en URLs, texto visible ni logs.

Bloqueo de contrato resuelto: nueva consulta GET /orders/{id}/payment, de solo lectura,
autorizada con get_owned_order y el PaymentResponse existente. No cambia estados ni esquema.
GET Order devuelve id/version/status/total/items; GET payment devuelve id/order_id/status/proof_reference.
La interfaz exige PENDIENTE_ACEPTACION, PENDIENTE_COMPROBANTE y proof_reference nulo.
Si falta una consulta, no ofrece edición; el backend decide nuevamente al guardar.
Las dos lecturas no son una instantánea conjunta: una carrera se resuelve con 409 en PUT.

La edición usa un borrador en memoria separado del carrito y consulta pedido/pago/catálogo
antes de iniciarlo. Se pueden agregar, quitar y cambiar opciones (cantidad, variante, sabores).
Cambiar opción abre el configurador existente; se elige de nuevo la cantidad y los sabores.
Los precios mostrados son estimados hasta guardar. PUT /orders/{id}/items envía solamente
expected_version y items[{variant_id,quantity,selected_flavors}], con el token de ese pedido.
El servidor devuelve el ID original, su total y la nueva versión.

Un conflicto bloquea nuevos envíos del borrador. Actualizar conserva el borrador y su versión;
para iniciar otra edición hay que descartarlo explícitamente. Nunca se actualiza expected_version
ni se reenvía automáticamente. Un PUT incierto consulta el estado real pero no afirma éxito;
se revisa el pedido actual antes de descartar el borrador. Salir de la pantalla o recargar
pierde el borrador de edición, no los pedidos ni el carrito nuevo. Productos históricos no
presentes en el catálogo quedan identificados por variante y requieren quitarse/reemplazarse.
Los estados 401/403 no crean pedidos de reemplazo ni recurren a credenciales de personal.
No se configuran instrucciones bancarias; la carga de comprobantes se describe a continuación.

Verificación adicional:

    node --test frontend/tests/*.test.mjs
    node frontend/tests/browser.mjs --orders

El segundo comando verifica el flujo móvil real con dos pedidos, edición, conflicto,
comprobante e invalidación de token contra una base SQLite aislada, sin tocar datos reales.

Para verificar el mismo flujo A/B en PostgreSQL aislado:

    .venv/Scripts/python.exe frontend/tests/browser_postgres.py --orders

Comprueba exactamente dos Orders, Payments y registros de idempotencia, versiones
3/1 y estados de pago separados. El esquema se elimina al terminar.

## Comprobante de transferencia del invitado

El detalle del pedido muestra ID, total confirmado y estado real del pago. El formulario
se ofrece solo para PENDIENTE_ACEPTACION + PENDIENTE_COMPROBANTE + proof_reference nulo.
No existen datos bancarios verificados configurados: se muestra un aviso de configuración
pendiente y se indica contactar al restaurante antes de transferir.

Contrato existente: POST /orders/{order_id}/payment/proof, con X-Order-Access-Token y
FormData: file (image/jpeg o image/png, 1 a 5.242.880 bytes) y expected_version
(entero positivo serializado como texto multipart). No requiere Payment ID ni JWT de staff.
El navegador genera Content-Type y boundary. El proxy conserva los bytes y la cabecera;
permite 5 MB de archivo + 64 KiB de envoltura multipart sin ampliar el límite de JSON.
Hay que reiniciar frontend/serve.py al actualizarlo; producción debe reenviar esta ruta.

El cliente valida MIME/tamaño, muestra nombre y vista previa local y exige confirmar el total.
Antes de enviar vuelve a consultar pedido/pago. Si cambió versión o total, actualiza lo
mostrado y exige nueva revisión sin enviar bytes. Las carreras posteriores las resuelve
el backend con expected_version: ante 409 se consulta el estado y no se reenvía el archivo.
La respuesta es PaymentResponse {id, order_id, status, proof_reference}; un envío exitoso
devuelve PENDIENTE_VERIFICACION, no APROBADO. Se bloquean nuevas subidas y la edición.
El backend rechaza repetir el comprobante con 409, sin sustituir el anterior.

Ante respuesta incierta se consulta el pago. Si ya hay comprobante, se muestra el estado
observado sin afirmar que se aprobó ni que se identificó nuestro archivo. Si sigue pendiente
o la consulta falla, no se permite reenviar hasta una consulta explícita y nueva revisión
del total. Nunca hay reintentos automáticos. No se crea otro Order ni se eliminan tokens.
Archivo, preview y confirmación viven en memoria; salir o recargar pierde la selección,
pero conserva accesos según sessionStorage y consulta el estado real al volver.

Validación de imagen sigue el contrato existente: MIME declarado y tamaño; no se añade
análisis del contenido, antivirus o verificación bancaria. El backend mantiene su limpieza
compensatoria de archivos fallidos; no hay transacción atómica entre disco y PostgreSQL.

    node --test frontend/tests/*.test.mjs
    .venv/Scripts/python.exe frontend/tests/browser_postgres.py --orders --proof
    .venv/Scripts/python.exe tests/run_order_modification_audit.py tests

El recorrido Chrome usa PostgreSQL y archivos temporales aislados, PNG real y JPEG real
de exactamente 5 MB. Fuerza 409 tras cambiar la versión y pierde una respuesta exitosa;
verifica IDs/totales/accesos, PENDIENTE_VERIFICACION, rechazo de edición y recarga.
Las capturas proof-*.png muestran el formulario móvil sin credenciales visibles.


## Recepcion operativa

Abrir #reception e iniciar sesion con un usuario RECEPTION existente mediante
POST /login {username,password}. El JWT vive solo en memoria; salir de la ruta,
recargar o cerrar sesion exige autenticarse nuevamente. No se crean usuarios.

Contratos: GET /payments/pending devuelve PaymentResponse[]; nueva consulta
GET /reception/orders/{order_id} devuelve OrderResponse y exige RECEPTION.
GET /payments/{payment_id}/proof devuelve PNG/JPEG protegido mediante Bearer;
se muestra como blob local y se revocan sus object URLs al actualizar/salir.
PATCH /payments/{payment_id}/status envia solo {status: APROBADO | RECHAZADO}.
Rechazar cancela el pedido segun la regla existente. No hay reenvio automatico.
Se exige inspeccion y confirmacion, se bloquean acciones pendientes y se vuelve
a consultar la cola tras toda decision. Una consulta fallida bloquea decisiones.
Los articulos usan nombres de GET /products, con ID de variante como alternativa
para productos historicos o catalogo no disponible, cantidades
y sabores; el total se muestra como lo devuelve el servidor, sin recalcularlo.
La cola no incluye pedidos sin comprobante: no existe esa consulta en la API.

El proxy tambien permite POST /login, GET /payments/pending,
GET /reception/orders/{id}, GET /payments/{id}/proof y PATCH /payments/{id}/status,
reenviando Authorization y conservando el cuerpo binario. Reiniciar serve.py.
Produccion debe configurar las mismas rutas, HTTPS y evitar logs de credenciales.
No hay cambios de esquema ni de reglas de pago/cocina.

Validacion: node --test frontend/tests/*.test.mjs y
.venv/Scripts/python.exe tests/run_order_modification_audit.py tests
(pruebas PostgreSQL confinadas a un esquema temporal).

Prueba adicional de interfaz real: node frontend/tests/reception-browser.mjs
(Chrome sin ventana, FastAPI/SQLite y comprobantes temporales; incluye bloqueo de
KITCHEN, confirmacion, doble clic, liberacion de URLs y ambos resultados).


## Cocina operativa

Abrir #kitchen con un usuario KITCHEN existente. POST /login y Bearer reutilizan
la autenticacion del personal; el JWT vive solo en memoria. GET /kitchen/orders
es una nueva consulta de pedidos activos con pago APROBADO y estados existentes
PENDIENTE_ACEPTACION, PREPARANDO y LISTO. GET /kitchen/orders/pending se conserva.
PATCH /kitchen/orders/{id}/status reutiliza el contrato existente, enviando solo
{status}: PREPARANDO, LISTO o ENVIADO. El servidor decide cada transicion.
GET /products aporta etiquetas; variantes fuera del catalogo muestran su ID.
No cambia el esquema ni las reglas de pago. Reiniciar el proxy de desarrollo;
produccion debe reenviar las nuevas rutas Kitchen con Authorization.

Actualizar conserva filtro y contexto de la pantalla. Doble clic queda bloqueado;
cada accion consulta nuevamente la cola incluso tras conflicto o respuesta incierta.
Si falla la consulta, los datos previos quedan visibles sin acciones habilitadas.
401/403 requieren volver a autenticarse. Recargar/salir pierde el JWT.
Pedidos ENVIADO salen de la cola. No hay aprobacion de pagos en Cocina.

Smoke manual: aprobar un pedido con alitas desde Recepcion; abrir #kitchen,
iniciar sesion, comprobar numero/cantidades/variante/sabores, aceptar, actualizar,
marcar LISTO y luego ENVIADO. Verificar que desaparece de activos.
Pruebas: node --test frontend/tests/*.test.mjs; node frontend/tests/kitchen-browser.mjs;
.venv/Scripts/python.exe tests/run_order_modification_audit.py tests
(PostgreSQL en esquema temporal aislado).
