import { ApiError, validOrder } from './api.js';

export const SESSION_KEY = 'bladys.guest-checkout.v1';
const clone = value => JSON.parse(JSON.stringify(value));
const fingerprint = payload => JSON.stringify(payload.items.map(i => [i.variant_id, i.quantity, [...i.selected_flavors].sort()]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))));

// Submitted payload/key/labels are immutable; only the attempt status changes.
export function createCheckout({ cart, send, storage, keyFactory = () => crypto.randomUUID() }) {
  let attempt = null, receipt = null, busy = false, inFlight = null, error = null, storageWarning = false;
  const listeners = new Set();
  const notify = () => listeners.forEach(fn => fn());
  const store = (overrides = {}) => {
    try {
      storage().setItem(SESSION_KEY, JSON.stringify({ cart: cart.items, attempt, receipt, ...overrides }));
      storageWarning = false; return true;
    } catch { storageWarning = true; return false; }
  };
  try {
    const saved = JSON.parse(storage().getItem(SESSION_KEY) || 'null');
    if (saved) {
      if (validOrder(saved.receipt?.order) && typeof saved.receipt.token === 'string' && saved.receipt.token) receipt = saved.receipt;
      if (typeof saved.attempt?.key === 'string' && saved.attempt.key
          && fingerprint(saved.attempt.payload) === saved.attempt.fingerprint) {
        attempt = clone(saved.attempt);
        // A previous page cannot know whether a submitted request finished.
        if (!['ready', 'rejected', 'confirmed'].includes(attempt.status)) attempt.status = 'uncertain';
        if (attempt.status === 'confirmed' && (!receipt || receipt.order.id !== attempt.orderId)) attempt.status = 'uncertain';
      }
      // Restore cart separately: a damaged draft must not destroy order recovery.
      try { for (const i of saved.cart || []) cart.add(i.product, i.variant, i.quantity, i.selected_flavors); }
      catch { cart.clear(); storageWarning = true; }
    }
  } catch { storageWarning = true; }
  function cartChanged() {
    // Only an unsent or definitively rejected attempt can be replaced by edits.
    if (attempt && ['ready', 'rejected'].includes(attempt.status)
        && attempt.fingerprint !== fingerprint(cart.payload())) { attempt = null; error = null; }
    store(); notify();
  }
  function saveConfirmed() {
    // First record credentials while retaining the cart and recovery snapshot.
    if (!store()) return receipt;
    const clear = fingerprint(cart.payload()) === attempt.fingerprint;
    // Persist cleanup before changing memory. If it fails, recovery stays explicit.
    if (!store({ cart: clear ? [] : cart.items, attempt: null })) return receipt;
    if (clear) cart.clear();
    attempt = null;
    return receipt;
  }
  function submit() {
    if (busy) return inFlight;
    if (attempt?.status === 'confirmed') {
      const result = saveConfirmed(); notify(); return Promise.resolve(result);
    }
    if (!attempt && !cart.count) { error = new ApiError('Tu carrito est\u00e1 vac\u00edo.'); notify(); return Promise.resolve(null); }
    if (!attempt) {
      try { attempt = { key: keyFactory(), fingerprint: fingerprint(cart.payload()), payload: clone(cart.payload()), items: clone(cart.items), status: 'ready' }; }
      catch {
        error = new ApiError('Abre el sitio con HTTPS o en localhost para confirmar de forma segura.');
        notify(); return Promise.resolve(null);
      }
    }
    error = null;
    const previousStatus = attempt.status;
    attempt.status = 'submitting';
    if (!store()) {
      attempt.status = previousStatus;
      error = new ApiError('Activa el almacenamiento de esta pesta\u00f1a para guardar un reintento seguro antes de confirmar.');
      notify(); return Promise.resolve(null);
    }
    const submitted = clone(attempt);
    busy = true;
    inFlight = Promise.resolve().then(async () => {
      try {
        const result = await send(submitted.payload, submitted.key);
        if (!validOrder(result) || typeof result.order_access_token !== 'string' || !result.order_access_token) throw new ApiError('Respuesta incompleta.', { uncertain: true });
        const { order_access_token: token, ...order } = result;
        receipt = { order, token, items: submitted.items };
        attempt.status = 'confirmed'; attempt.orderId = order.id;
        return saveConfirmed();
      } catch (failure) {
        error = failure instanceof ApiError ? failure : new ApiError('No pudimos confirmar el resultado.', { uncertain: true });
        attempt.status = error.uncertain ? 'uncertain' : 'rejected';
        store(); return null;
      } finally { busy = false; inFlight = null; notify(); }
    });
    notify();
    return inFlight;
  }
  return {
    get busy() { return busy; }, get error() { return error; }, get receipt() { return receipt; },
    get pending() { return Boolean(attempt); }, get storageWarning() { return storageWarning; },
    get confirmedRecovery() { return attempt?.status === 'confirmed'; },
    get reviewItems() { return clone(attempt ? attempt.items : cart.items); },
    get draftDiffers() { return Boolean(attempt && attempt.fingerprint !== fingerprint(cart.payload())); },
    subscribe(fn) { listeners.add(fn); fn(); return () => listeners.delete(fn); }, cartChanged, submit,
    updateOrder(order) {
      if (receipt && validOrder(order) && receipt.order.id === order.id) { receipt.order = clone(order); store(); notify(); }
    },
  };
}
