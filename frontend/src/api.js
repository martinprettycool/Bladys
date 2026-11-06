// Same-origin by default; production hosting must proxy these routes to FastAPI.
export class ApiError extends Error {
  constructor(message, { status = 0, uncertain = false, code = null, currentVersion = null } = {}) {
    super(message); this.status = status; this.uncertain = uncertain; this.code = code; this.currentVersion = currentVersion;
  }
}
const base = () => (globalThis.document?.querySelector('meta[name="api-base"]')?.content || '').replace(/\/$/, '');
export function validOrder(order) {
  return Number.isSafeInteger(order?.id) && order.id > 0 && typeof order.status === 'string'
    && /^\d+(\.\d{1,2})?$/.test(String(order.total)) && Array.isArray(order.items) && order.items.length > 0
    && order.items.every(i => Number.isSafeInteger(i.variant_id) && Number.isSafeInteger(i.quantity) && i.quantity > 0
      && /^\d+(\.\d{1,2})?$/.test(String(i.unit_price)) && Array.isArray(i.selected_flavors)
      && i.selected_flavors.every(f => typeof f === 'string'));
}
export async function request(path, { timeout = 15000, fetchImpl = globalThis.fetch, ...options } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetchImpl(base() + path, { ...options, signal: controller.signal, cache: 'no-store' });
    let body;
    try { body = await response.json(); } catch {
      throw new ApiError('La respuesta no pudo verificarse.', { status: response.status, uncertain: response.ok || response.status >= 500 || response.status === 408 });
    }
    if (!response.ok) {
      const messages = {
        400: 'Una variante ya no est\u00e1 disponible. Revisa el carrito.',
        404: 'Un producto o pedido ya no est\u00e1 disponible.',
        409: 'El intento entra en conflicto con el servidor. No se crear\u00e1 otra clave autom\u00e1ticamente.',
        422: 'Revisa las cantidades, variantes y sabores del carrito.',
        401: 'El acceso a este pedido expir\u00f3 o no es v\u00e1lido.',
        403: 'No tienes acceso a este pedido.',
      };
      const detail = typeof body.detail === 'string' && response.status < 500 ? ' ' + body.detail : '';
      throw new ApiError((messages[response.status] || 'El restaurante no pudo procesar la solicitud.') + detail,
        { status: response.status, uncertain: response.status >= 500 || response.status === 408, code: body.detail?.code, currentVersion: body.detail?.current_version });
    }
    return body;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('No pudimos confirmar la respuesta del restaurante.', { uncertain: true });
  } finally { clearTimeout(timer); }
}
export async function getProducts({ signal } = {}) {
  const response = await fetch(`${base()}/products`, { signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (!response.ok) throw new Error('Cat\u00e1logo no disponible');
  const products = await response.json();
  if (!Array.isArray(products)) throw new Error('Respuesta de cat\u00e1logo inv\u00e1lida');
  return products;
}
export async function createOrder(payload, key, options = {}) {
  const order = await request('/orders', { ...options, method: 'POST', headers: {
    Accept: 'application/json', 'Content-Type': 'application/json', 'Idempotency-Key': key,
  }, body: JSON.stringify(payload) });
  if (!validOrder(order) || typeof order.order_access_token !== 'string' || !order.order_access_token) {
    throw new ApiError('La respuesta del pedido est\u00e1 incompleta. Reintenta con la misma selecci\u00f3n.', { uncertain: true });
  }
  return order;
}
export async function getOrder(id, token, options = {}) {
  const order = await request('/orders/' + id, { ...options, headers: { Accept: 'application/json', 'X-Order-Access-Token': token } });
  if (!validOrder(order) || order.id !== id) throw new ApiError('No pudimos verificar el pedido.');
  return order;
}

export async function getOrderPayment(id, token, options = {}) {
  const payment = await request(`/orders/${id}/payment`, { ...options, headers: { Accept: 'application/json', 'X-Order-Access-Token': token } });
  if (payment.order_id !== id || !['PENDIENTE_COMPROBANTE', 'PENDIENTE_VERIFICACION', 'APROBADO', 'RECHAZADO'].includes(payment.status)
      || !(payment.proof_reference === null || typeof payment.proof_reference === 'string')) throw new ApiError('No pudimos verificar el pago.');
  return payment;
}
export async function replaceOrder(id, token, payload, options = {}) {
  const order = await request(`/orders/${id}/items`, { ...options, method: 'PUT', headers: {
    Accept: 'application/json', 'Content-Type': 'application/json', 'X-Order-Access-Token': token,
  }, body: JSON.stringify(payload) });
  if (!validOrder(order) || order.id !== id || !Number.isSafeInteger(order.version) || order.version !== payload.expected_version + 1)
    throw new ApiError('No pudimos verificar la modificación. Consulta el pedido.', { uncertain: true });
  return order;
}

export async function uploadOrderProof(id, token, file, expectedVersion, options = {}) {
  const body = new FormData();
  body.append('file', file, file.name);
  body.append('expected_version', String(expectedVersion));
  const payment = await request(`/orders/${id}/payment/proof`, { ...options, method: 'POST',
    headers: { Accept: 'application/json', 'X-Order-Access-Token': token }, body });
  if (payment.order_id !== id || payment.status !== 'PENDIENTE_VERIFICACION' || typeof payment.proof_reference !== 'string' || !payment.proof_reference)
    throw new ApiError('No pudimos verificar el comprobante. Consulta el estado del pago.', { uncertain: true });
  return payment;
}
