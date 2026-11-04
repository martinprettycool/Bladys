import test from 'node:test';
import assert from 'node:assert/strict';
import { createCart } from '../src/cart.js';
import { createCheckout, SESSION_KEY } from '../src/checkout-state.js';
import { createOrder, getOrder, ApiError } from '../src/api.js';
const product = { id: 1, name: 'Alitas', flavor_options: ['BBQ', 'Buffalo'], is_available: true };
const variant = { id: 42, name: '12 piezas', price: '8.50', is_available: true, max_flavors: 2 };
const result = { id: 731, status: 'PENDIENTE_ACEPTACION', total: '20.00', order_access_token: 'test-capability', items: [
  { variant_id: 42, quantity: 2, unit_price: '10.00', selected_flavors: ['BBQ', 'Buffalo'] },
] };
const memory = () => { const data = new Map(); return { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value) }; };
function setup(send = async () => result, store = memory()) {
  const cart = createCart(); cart.add(product, variant, 2, ['Buffalo', 'BBQ']);
  let keys = 0;
  const checkout = createCheckout({ cart, send, storage: () => store, keyFactory: () => `attempt-${++keys}` });
  return { cart, checkout, store };
}

test('checkout sends only IDs, quantities and selected flavors; uses server receipt and persists capability', async () => {
  const calls = [];
  const { cart, checkout, store } = setup(async (...args) => { calls.push(args); return result; });
  assert.equal(cart.total, 1700);
  const receipt = await checkout.submit();
  assert.deepEqual(calls[0][0], { items: [{ variant_id: 42, quantity: 2, selected_flavors: ['BBQ', 'Buffalo'] }] });
  assert.equal(receipt.order.id, 731); assert.equal(receipt.order.total, '20.00');
  assert.equal(receipt.token, result.order_access_token);
  assert.equal('order_access_token' in receipt.order, false);
  assert.equal(cart.count, 0);
  const restored = createCheckout({ cart: createCart(), send: async () => result, storage: () => store });
  assert.deepEqual(restored.receipt, receipt);
  assert.equal(JSON.parse(store.getItem(SESSION_KEY)).attempt, null);
});

test('double submission shares one in-flight request and one logical order', async () => {
  // Contexto: dos confirmaciones rapidas del mismo carrito.
  // Garantia: solo se envia una solicitud mientras la primera sigue pendiente.
  // Escenarios: doble clic antes de la respuesta y confirmacion posterior.
  let resolve, calls = 0;
  const { checkout, cart } = setup(() => { calls++; return new Promise(done => { resolve = done; }); });
  const first = checkout.submit(), second = checkout.submit();
  assert.equal(first, second); assert.equal(checkout.busy, true);
  await Promise.resolve(); assert.equal(calls, 1);
  resolve(result); await first;
  assert.equal(checkout.busy, false); assert.equal(cart.count, 0);
  await checkout.submit(); assert.equal(calls, 1);
});

test('uncertain failure preserves cart, exact payload and key across retry and reload', async () => {
  // Contexto: el servidor puede haber creado el pedido antes de perderse la respuesta.
  // Garantia: recargar y reintentar conserva la identidad y solicitud del intento.
  // Escenarios: fallo de red, recarga de pestaña y recuperacion exitosa.
  const calls = [];
  const { checkout, cart, store } = setup(async (payload, key) => { calls.push({ payload, key }); throw new ApiError('timeout', { uncertain: true }); });
  const before = structuredClone(cart.payload());
  assert.equal(await checkout.submit(), null); assert.deepEqual(cart.payload(), before);
  assert.equal(checkout.error.uncertain, true); assert.equal(checkout.receipt, null);
  const restoredCart = createCart();
  const restored = createCheckout({ cart: restoredCart, storage: () => store, send: async (payload, key) => {
    calls.push({ payload, key }); return result;
  } });
  assert.deepEqual(restoredCart.payload(), before);
  await restored.submit(); assert.deepEqual(calls[0], calls[1]);
});

test('edits cannot replace uncertain checkout; next purchase gets a new key after recovery', async () => {
  // Contexto: el cliente edita el carrito tras perder la respuesta del pedido.
  // Garantia: primero recupera la solicitud original; una compra posterior usa otra clave.
  // Escenarios: fallo incierto, edicion, reintento seguro y compra nueva.
  const calls = []; let fail = true;
  const { checkout, cart } = setup(async (payload, key) => {
    calls.push({ payload: structuredClone(payload), key });
    if (fail) { fail = false; throw new ApiError('network', { uncertain: true }); }
    return result;
  });
  await checkout.submit();
  cart.change(cart.items[0].key, 1); checkout.cartChanged();
  await checkout.submit();
  assert.deepEqual(calls[0], calls[1]); assert.equal(cart.count, 3);
  await checkout.submit(); assert.notEqual(calls[1].key, calls[2].key);
  assert.equal(calls[2].payload.items[0].quantity, 3);
});

for (const status of [400, 404, 409, 422, 500]) test(`HTTP ${status} preserves cart and does not fabricate confirmation`, async () => {
  const { cart, checkout } = setup(async () => { throw new ApiError('rejected', { status, uncertain: status >= 500 }); });
  const before = structuredClone(cart.payload()); await checkout.submit();
  assert.deepEqual(cart.payload(), before); assert.equal(checkout.receipt, null); assert.equal(checkout.error.status, status);
});

test('empty cart and unavailable session persistence never send a request', async () => {
  let calls = 0;
  const { cart, checkout } = setup(async () => { calls++; return result; });
  cart.clear(); await checkout.submit(); assert.equal(calls, 0);
  const blocked = setup(async () => { calls++; return result; }, { getItem: () => null, setItem: () => { throw Error('blocked'); } });
  await blocked.checkout.submit(); assert.equal(calls, 0); assert.equal(blocked.cart.count, 2);
});

test('success does not discard cart changes made while the request is pending', async () => {
  // Contexto: el cliente vuelve al menu mientras espera la confirmacion.
  // Garantia: la respuesta del carrito anterior no borra la nueva seleccion.
  // Escenarios: envio pendiente, cambio de cantidad y llegada de respuesta exitosa.
  let resolve;
  const { cart, checkout } = setup(() => new Promise(done => { resolve = done; }));
  const pending = checkout.submit(); await Promise.resolve();
  cart.change(cart.items[0].key, 1); checkout.cartChanged();
  resolve(result); await pending;
  assert.equal(cart.count, 3); assert.equal(checkout.receipt.order.items[0].quantity, 2);
});

test('API sends idempotency and capability headers without staff Authorization', async () => {
  const calls = [];
  const fetchImpl = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify(result), { status: 201 }); };
  await createOrder({ items: [{ variant_id: 42, quantity: 2, selected_flavors: ['BBQ'] }] }, 'stable-key', { fetchImpl });
  assert.equal(calls[0].url, '/orders'); assert.equal(calls[0].options.headers['Idempotency-Key'], 'stable-key');
  assert.deepEqual(JSON.parse(calls[0].options.body).items[0].selected_flavors, ['BBQ']);
  await getOrder(731, 'guest-capability', { fetchImpl });
  assert.equal(calls[1].url, '/orders/731'); assert.equal(calls[1].options.headers['X-Order-Access-Token'], 'guest-capability');
  assert.equal('Authorization' in calls[1].options.headers, false);
  assert.equal(calls[1].url.includes('capability'), false);
});

test('timeout and malformed success are uncertain, not successful orders', async () => {
  await assert.rejects(createOrder({}, 'same-key', { timeout: 5, fetchImpl: async (_, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) }), e => e.uncertain === true);
  await assert.rejects(createOrder({}, 'same-key', { fetchImpl: async () => new Response('{}', { status: 201 }) }), e => e.uncertain === true);
  await assert.rejects(createOrder({}, 'same-key', { fetchImpl: async () => new Response(JSON.stringify({ detail: 'invalid flavors' }), { status: 422 }) }), e => e.status === 422 && !e.uncertain);
});


test('storage failure after success keeps the real receipt and warns without resubmission', async () => {
  // Contexto: el pedido se crea, pero el navegador deja de permitir guardar la sesion.
  // Garantia: una respuesta confirmada se conserva en memoria sin enviar otro pedido.
  // Escenarios: almacenamiento previo correcto, fallo posterior y acceso al recibo.
  const store = memory(); let blocked = false, calls = 0;
  const protectedStore = { getItem: key => store.getItem(key), setItem: (key, value) => {
    if (blocked) throw Error('quota'); store.setItem(key, value);
  } };
  const { cart, checkout } = setup(async () => { calls++; blocked = true; return result; }, protectedStore);
  const receipt = await checkout.submit();
  assert.equal(receipt.order.id, result.id); assert.equal(checkout.storageWarning, true);
  assert.equal(cart.count, 2); assert.equal(calls, 1);
  await checkout.submit(); assert.equal(calls, 1);
  blocked = false;
  await checkout.submit(); assert.equal(calls, 1); assert.equal(cart.count, 0);
  const recovered = createCheckout({ cart: createCart(), storage: () => store, send: async () => { calls++; return result; } });
  await recovered.submit(); assert.equal(recovered.receipt.order.id, result.id);
  assert.equal(calls, 1); // Local credential recovery never repeats POST /orders.
});

test('missing secure UUID generation fails before submitting and preserves the cart', async () => {
  let calls = 0;
  const cart = createCart(); cart.add(product, variant, 1, ['BBQ']);
  const checkout = createCheckout({ cart, send: async () => { calls++; return result; }, storage: memory,
    keyFactory: () => { throw Error('insecure context'); } });
  assert.equal(await checkout.submit(), null); assert.equal(calls, 0); assert.equal(cart.count, 1);
  assert.match(checkout.error.message, /HTTPS/);
});


test('restored uncertain snapshot retries original selection even with a different or empty draft', async () => {
  // Contexto: se edita o vacia el carrito mientras se desconoce el resultado original.
  // Garantia: la recarga conserva payload y clave originales sin perder el borrador.
  // Escenarios: edicion o vaciado, recarga y recuperacion del pedido enviado.
  for (const empty of [false, true]) {
    const calls = [];
    const { checkout, cart, store } = setup(async (payload, key) => {
      calls.push({ payload: structuredClone(payload), key }); throw new ApiError('lost', { uncertain: true });
    });
    await checkout.submit();
    if (empty) cart.clear(); else cart.change(cart.items[0].key, 1);
    checkout.cartChanged();
    const draft = structuredClone(cart.payload());
    const restoredCart = createCart();
    const restored = createCheckout({ cart: restoredCart, storage: () => store, send: async (payload, key) => {
      calls.push({ payload: structuredClone(payload), key }); return result;
    } });
    assert.equal(restored.pending, true); assert.equal(restored.draftDiffers, true);
    assert.equal(restored.reviewItems[0].quantity, 2);
    assert.equal(JSON.parse(store.getItem(SESSION_KEY)).attempt.status, 'uncertain');
    await restored.submit();
    assert.deepEqual(calls[0], calls[1]); assert.deepEqual(restoredCart.payload(), draft);
    assert.equal(restored.receipt.order.id, result.id);
  }
});

test('credentials are persisted before clear and cleanup failure reload never sends another POST', async () => {
  // Contexto: el guardado del recibo funciona, pero falla el guardado de limpieza.
  // Garantia: la recarga reconoce el pedido confirmado y solo reintenta persistencia local.
  // Escenarios: recibo guardado, limpieza fallida, recarga y recuperacion sin otro envio.
  const backing = memory(); let writes = 0, calls = 0;
  const store = { getItem: key => backing.getItem(key), setItem: (key, value) => {
    if (++writes === 3) throw Error('cleanup failed'); backing.setItem(key, value);
  } };
  const { cart, checkout } = setup(async () => { calls++; return result; }, store);
  await checkout.submit();
  assert.equal(cart.count, 2); assert.equal(checkout.confirmedRecovery, true);
  const saved = JSON.parse(backing.getItem(SESSION_KEY));
  assert.equal(saved.receipt.order.id, result.id); assert.equal(saved.receipt.token, result.order_access_token);
  assert.equal(saved.attempt.status, 'confirmed');
  const restoredCart = createCart();
  const restored = createCheckout({ cart: restoredCart, storage: () => backing, send: async () => { calls++; return result; } });
  const clear = restoredCart.clear;
  restoredCart.clear = () => {
    const persisted = JSON.parse(backing.getItem(SESSION_KEY));
    assert.equal(persisted.receipt.order.id, result.id); assert.equal(persisted.receipt.token, result.order_access_token);
    clear();
  };
  await restored.submit();
  assert.equal(calls, 1); assert.equal(restoredCart.count, 0); assert.equal(restored.pending, false);
});

test('definitively rejected checkout can be replaced by an edited selection with a new key', async () => {
  // Contexto: el backend rechaza sabores y el cliente cambia su seleccion.
  // Garantia: el intento rechazado no bloquea una compra corregida con clave nueva.
  // Escenarios: rechazo 422, cambio de cantidad y nueva confirmacion.
  const calls = [];
  const { cart, checkout } = setup(async (payload, key) => {
    calls.push({ payload, key });
    if (calls.length === 1) throw new ApiError('invalid', { status: 422 });
    return result;
  });
  await checkout.submit(); cart.change(cart.items[0].key, 1); checkout.cartChanged();
  await checkout.submit(); assert.notEqual(calls[0].key, calls[1].key);
  assert.equal(calls[1].payload.items[0].quantity, 3);
});
