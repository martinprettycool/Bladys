import test from 'node:test';
import assert from 'node:assert/strict';
import { createCheckout, SESSION_KEY } from '../src/checkout-state.js';
import { createCart } from '../src/cart.js';
import { createOrderEditor, editingReason } from '../src/order-editor.js';
import { ApiError, replaceOrder, getOrderPayment } from '../src/api.js';
const product = { id: 1, name: 'Pizza', is_available: true, flavor_options: [], variants: [] };
const variant = { id: 10, name: 'Grande', price: '5.00', is_available: true, max_flavors: null };
product.variants = [variant];
const order = (id = 1, quantity = 1, version = 1) => ({ id, version, status: 'PENDIENTE_ACEPTACION', total: `${quantity * 5}.00`,
  items: [{ variant_id: 10, quantity, unit_price: '5.00', selected_flavors: [] }] });
const receipt = () => ({ order: order(), token: 'guest-A', items: [] });
const payment = { order_id: 1, status: 'PENDIENTE_COMPROBANTE', proof_reference: null };
const memory = () => { const values = new Map(); return { getItem: k => values.get(k) || null, setItem: (k, v) => values.set(k, v) }; };

test('two guest purchases retain separate capabilities after reload; editing A leaves B and new cart unchanged', async () => {
  const storage = memory(), cart = createCart(); let id = 0;
  const checkout = createCheckout({ cart, storage: () => storage, keyFactory: () => `key-${id}`, send: async () => ({ ...order(++id), order_access_token: `guest-${id}` }) });
  cart.add(product, variant, 1); await checkout.submit();
  cart.add(product, variant, 1); await checkout.submit();
  const restored = createCheckout({ cart: createCart(), storage: () => storage });
  assert.deepEqual(restored.receipts.map(r => [r.order.id, r.token]), [[1, 'guest-1'], [2, 'guest-2']]);
  cart.add(product, variant, 3);
  checkout.updateOrder(order(1, 4, 2));
  assert.equal(checkout.receipt.order.id, 2);
  assert.equal(checkout.receipts[1].order.total, '5.00');
  assert.equal(checkout.receipts[0].order.version, 2);
  assert.equal(cart.count, 3);
  assert.equal(JSON.parse(storage.getItem(SESSION_KEY)).receipts.length, 2);
});
test('legacy session receipt migrates without inventing order history', () => {
  const storage = memory(); storage.setItem(SESSION_KEY, JSON.stringify({ receipt: receipt(), cart: [] }));
  assert.equal(createCheckout({ cart: createCart(), storage: () => storage }).receipts.length, 1);
});
test('editor sends PUT identity and original version with IDs only, preserving server total', async () => {
  let received, updated;
  const original = receipt();
  const editor = createOrderEditor({ receipt: original, products: [product], put: async (...args) => { received = args; return { ...order(1, 2, 2), total: '11.75' }; }, onUpdate: value => updated = value });
  editor.draft.change(editor.draft.items[0].key, 1);
  assert.equal(await editor.save(), true);
  assert.deepEqual(received, [1, 'guest-A', { expected_version: 1, items: [{ variant_id: 10, quantity: 2, selected_flavors: [] }] }]);
  assert.equal(updated.total, '11.75'); assert.equal(updated.id, 1); assert.equal(updated.version, 2);
  assert.equal(original.order.items[0].quantity, 1);
});
test('stale draft survives refresh and cannot silently adopt a new version or retry', async () => {
  /* Contexto: otro cliente guarda primero una edición.
     Garantía: refrescar conserva el borrador sin reenviarlo con la versión nueva.
     Escenarios: conflicto 409, consulta de versión 2 y segundo intento bloqueado. */
  let calls = 0;
  const editor = createOrderEditor({ receipt: receipt(), products: [product], put: async () => { calls++; throw new ApiError('Conflict', { status: 409, code: 'ORDER_VERSION_CONFLICT' }); }, read: async () => order(1, 5, 2), readPayment: async () => payment });
  editor.draft.change(editor.draft.items[0].key, 2);
  assert.equal(await editor.save(), false); await editor.refresh();
  assert.equal(editor.draft.count, 3); assert.equal(editor.expectedVersion, 1); assert.equal(editor.current.version, 2);
  assert.equal(await editor.save(), false); assert.equal(calls, 1); assert.match(editor.message, /borrador/);
});
test('uncertain PUT reconciles with GET and never automatically repeats the write or claims success', async () => {
  /* Contexto: se pierde la respuesta tras una posible modificación confirmada.
     Garantía: se consulta el estado real sin repetir la escritura ni asumir éxito.
     Escenarios: PUT incierto y GET con la selección ya modificada. */
  let writes = 0, reads = 0;
  const editor = createOrderEditor({ receipt: receipt(), products: [product], put: async () => { writes++; throw new ApiError('offline', { uncertain: true }); }, read: async () => { reads++; return order(1, 3, 2); }, readPayment: async () => payment });
  editor.draft.change(editor.draft.items[0].key, 2);
  assert.equal(await editor.save(), false); assert.equal(await editor.save(), false);
  assert.equal(writes, 1); assert.equal(reads, 1); assert.equal(editor.current.version, 2); assert.equal(editor.blocked, true);
});
for (const status of [401, 403]) test(`invalid or expired capability ${status} blocks editing without fallback`, async () => {
  const editor = createOrderEditor({ receipt: receipt(), products: [product], put: async () => { throw new ApiError('Denied', { status }); } });
  assert.equal(await editor.save(), false); assert.equal(editor.blocked, true); assert.match(editor.message, /expiró/);
});
test('editability fails closed for missing payment, submitted proof, approval and advanced orders', () => {
  assert.equal(editingReason(order(), payment), '');
  for (const p of [null, { ...payment, proof_reference: 'proof' }, { ...payment, status: 'PENDIENTE_VERIFICACION' }, { ...payment, status: 'APROBADO' }]) assert.ok(editingReason(order(), p));
  assert.ok(editingReason({ ...order(), status: 'PREPARANDO' }, payment));
});
test('empty draft never sends PUT', async () => {
  let calls = 0; const editor = createOrderEditor({ receipt: receipt(), products: [product], put: async () => { calls++; } });
  editor.draft.clear(); assert.equal(await editor.save(), false); assert.equal(calls, 0);
});
test('API preserves conflict code; PUT uses capability header, no credential URL or POST', async () => {
  const payload = { expected_version: 1, items: order().items.map(({ unit_price, ...i }) => i) };
  await assert.rejects(replaceOrder(1, 'secret', payload, { fetchImpl: async (url, options) => {
    assert.equal(url, '/orders/1/items'); assert.equal(options.method, 'PUT'); assert.equal(options.headers['X-Order-Access-Token'], 'secret'); assert.equal(options.headers.Authorization, undefined);
    assert.deepEqual(JSON.parse(options.body), payload);
    return new Response(JSON.stringify({ detail: { code: 'ORDER_VERSION_CONFLICT', current_version: 2 } }), { status: 409 });
  } }), e => e.code === 'ORDER_VERSION_CONFLICT' && e.currentVersion === 2);
});
test('payment API uses matching order capability and rejects mismatched responses', async () => {
  await assert.rejects(getOrderPayment(1, 'secret', { fetchImpl: async (url, options) => {
    assert.equal(url, '/orders/1/payment'); assert.equal(options.headers['X-Order-Access-Token'], 'secret');
    return Response.json({ ...payment, order_id: 2 });
  } }), /verificar el pago/);
});

for (const outcome of ['proof', 'new-version', 'offline']) test(`refresh during edit disables saving when state is ${outcome}`, async () => {
  let writes = 0;
  const editor = createOrderEditor({ receipt: receipt(), products: [product],
    read: async () => order(1, 1, outcome === 'new-version' ? 2 : 1),
    readPayment: async () => { if (outcome === 'offline') throw new ApiError('offline'); return outcome === 'proof' ? { ...payment, proof_reference: 'proof' } : payment; },
    put: async () => { writes++; },
  });
  if (outcome === 'offline') await assert.rejects(editor.refresh()); else await editor.refresh();
  assert.equal(editor.blocked, true); assert.equal(await editor.save(), false); assert.equal(writes, 0);
});
test('failure persisting purchase B retains purchase A and never clears B cart prematurely', async () => {
  /* Contexto: se confirma la segunda compra pero falla el almacenamiento.
     Garantia: el primer acceso sigue guardado y la segunda compra se recupera sin duplicarla.
     Escenarios: A persistida, B confirmada con fallo y guardado local posterior. */
  const backing = memory(); let blocked = false, id = 0, calls = 0;
  const storage = { getItem: k => backing.getItem(k), setItem: (k,v) => { if (blocked) throw Error('quota'); backing.setItem(k,v); } };
  const cart = createCart();
  const checkout = createCheckout({ cart, storage: () => storage, keyFactory: () => `purchase-${id}`, send: async () => { calls++; const next = ++id; if (next === 2) blocked = true; return { ...order(next), order_access_token: `cap-${next}` }; } });
  cart.add(product, variant, 1); await checkout.submit();
  cart.add(product, variant, 1); await checkout.submit();
  assert.equal(cart.count, 1); assert.equal(JSON.parse(backing.getItem(SESSION_KEY)).receipts[0].order.id, 1);
  blocked = false; await checkout.submit();
  assert.equal(calls, 2); assert.equal(cart.count, 0);
  assert.deepEqual(JSON.parse(backing.getItem(SESSION_KEY)).receipts.map(r=>r.order.id), [1,2]);
});
