# Blady's customer menu and checkout

Buildless HTML, CSS and native JavaScript modules; no runtime dependencies.
Start FastAPI with the project's configured PostgreSQL database and migrations,
then run from the repository root:

    python frontend/serve.py

Open http://127.0.0.1:5173. Use --backend and --port to override the API origin
and frontend port. Restart this server after updating serve.py. Static files use
Cache-Control: no-store during development to avoid stale JS and CSS.

## API integration

The development server proxies only these customer routes:

- GET /products
- POST /orders (forwards Idempotency-Key and JSON body)
- GET /orders/{id} (forwards X-Order-Access-Token)

HTTP error status and body are preserved. Production hosting must proxy these
routes to FastAPI as well. Optional meta[name="api-base"] can select an API origin
with CORS allowing Content-Type, Idempotency-Key and X-Order-Access-Token.
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
payment or approval. Proof submission is outside this iteration.

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
or staff JWT storage. Only the latest confirmed guest order is retained.

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
- src/app.js: hash routing/header; staff/foundation previews preserved.
- src/configurator.js: existing variant/flavor dialogs.
- src/category-card.js: centralized API category to local illustration mapping.
- styles/app.css: existing design tokens/layout with scoped checkout additions.
- serve.py: development static server and customer API proxy.

Mobile uses a compact 2-column category grid, 4 columns at 768px. Supplied AI
illustrations in public/menu use responsive 400/800px JPEG derivatives with
lazy loading and a text fallback. Product rows avoid repetitive photos. Customer
routes include #menu, #category-pizza, #category-hamburguesa, #category-alitas,
#category-papitas, #checkout and #my-order; #pizzas remains an alias. Staff routes
#reception/#kitchen and #foundation remain preview shells.

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
other browsers, payment proof upload, or production deployment.
