import test from 'node:test';
import assert from 'node:assert/strict';
import { createCart } from '../src/cart.js';
import { createCheckout, SESSION_KEY } from '../src/checkout-state.js';
import { createOrder, ApiError } from '../src/api.js';

const product = { id: 1, name: 'Alitas', is_available: true, flavor_options: ['BBQ', 'Buffalo'] };
const variant = { id: 42, name: '12 piezas', price: '8.50', max_flavors: 2, is_available: true };
const receipt = { id: 731, status: 'PENDIENTE_ACEPTACION', total: '17.00', order_access_token: 'test-only-guest-token',
  items: [{ variant_id: 42, quantity: 2, unit_price: '8.50', selected_flavors: ['BBQ'] }] };
const memory = () => {
  const data = new Map();
  return { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value) };
};
function populatedCart() {
  const cart = createCart(); cart.add(product, variant, 2, ['BBQ']); return cart;
}

test('B: cart remains until successful Order credentials are durably recorded', async () => {
  /* Contexto: FastAPI confirma el pedido, pero falla el guardado de sus credenciales.
     Garantia: el carrito no se vacia antes de preservar ID y token en la sesion.
     Escenarios: guardado previo correcto y fallo de almacenamiento tras la respuesta. */
  const cart = populatedCart(), backing = memory(); let blocked = false;
  const store = { getItem: key => backing.getItem(key), setItem: (key, value) => {
    if (blocked) throw Error('quota exceeded'); backing.setItem(key, value);
  } };
  const checkout = createCheckout({ cart, storage: () => store, keyFactory: () => 'original-key',
    send: async () => { blocked = true; return receipt; } });
  await checkout.submit();
  assert.equal(checkout.storageWarning, true);
  assert.equal(JSON.parse(backing.getItem(SESSION_KEY)).receipt, null);
  assert.equal(cart.count, 2, 'cart was cleared even though successful credentials were not persisted');
});

test('C: an in-flight attempt remains recoverable when draft changes and page reloads', async () => {
  /* Contexto: el cliente vuelve al menu y modifica el carrito durante una solicitud.
     Garantia: recargar no elimina la unica clave que permite recuperar el primer pedido.
     Escenarios: envio pendiente, cambio de cantidad, recarga y respuesta incierta. */
  const store = memory(), cart = populatedCart(); let reject;
  const checkout = createCheckout({ cart, storage: () => store, keyFactory: () => 'original-key',
    send: () => new Promise((_, fail) => { reject = fail; }) });
  const pending = checkout.submit(); await Promise.resolve();
  try {
    cart.change(cart.items[0].key, 1); checkout.cartChanged();
    const saved = JSON.parse(store.getItem(SESSION_KEY));
    assert.equal(saved.attempt.key, 'original-key');
    assert.equal(saved.attempt.payload.items[0].quantity, 2);
    assert.equal(saved.cart[0].quantity, 3);
    const restored = createCheckout({ cart: createCart(), storage: () => store, send: async () => receipt });
    assert.equal(restored.pending, true, 'reload discards the unresolved attempt because draft differs');
  } finally { reject(new ApiError('connection lost', { uncertain: true })); await pending; }
});

test('E: a non-JSON HTTP 408 remains an uncertain timeout outcome', async () => {
  /* Contexto: el proxy devuelve una pagina HTML de timeout en lugar de JSON.
     Garantia: un timeout no se presenta como rechazo definitivo del pedido.
     Escenarios: HTTP 408 con cuerpo no JSON tras enviar la solicitud. */
  await assert.rejects(createOrder({ items: [] }, 'original-key', {
    fetchImpl: async () => new Response('<html>Request Timeout</html>', { status: 408 }),
  }), error => {
    assert.equal(error.status, 408);
    assert.equal(error.uncertain, true, 'non-JSON timeout is classified as definitive');
    return true;
  });
});

for (const mode of ['offline', 'timeout', 'http500', 'http409', 'http422']) {
  test(`E: actual API ${mode} preserves cart and key for an unchanged retry`, async () => {
    /* Contexto: falla la confirmacion sin que el cliente cambie su seleccion.
       Garantia: carrito y clave se conservan, sin inventar un pedido confirmado.
       Escenarios: error de red, timeout, error de servidor, conflicto o validacion. */
    const cart = populatedCart(), store = memory(), calls = [];
    const before = cart.payload();
    let failed = false;
    const fetchImpl = async (_, options) => {
      calls.push({ key: options.headers['Idempotency-Key'], body: options.body });
      if (!failed) {
        failed = true;
        if (mode === 'offline') throw new TypeError('network unavailable');
        if (mode === 'timeout') return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
        return new Response(JSON.stringify({ detail: 'test rejection' }), { status: Number(mode.slice(4)) });
      }
      return new Response(JSON.stringify(receipt), { status: 201 });
    };
    let generated = 0;
    const checkout = createCheckout({ cart, storage: () => store, keyFactory: () => `key-${++generated}`,
      send: (payload, key) => createOrder(payload, key, { fetchImpl, timeout: 5 }) });
    assert.equal(await checkout.submit(), null);
    assert.deepEqual(cart.payload(), before); assert.equal(checkout.receipt, null);
    assert.equal(checkout.error.uncertain, ['offline', 'timeout', 'http500'].includes(mode));
    await checkout.submit();
    assert.deepEqual(calls[0], calls[1]); assert.equal(generated, 1);
    assert.equal(checkout.receipt.order.id, receipt.id);
  });
}
