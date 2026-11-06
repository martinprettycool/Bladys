import { request, ApiError } from './api.js';
const base = () => (globalThis.document?.querySelector('meta[name="api-base"]')?.content || '').replace(/\/$/, '');
export function staffMessage(error) {
  return ({401: 'Sesion invalida o vencida. Inicia sesion nuevamente.', 403: 'Tu usuario no tiene permiso de RECEPTION.',
    404: 'El pedido o comprobante ya no esta disponible.',
    409: 'Otro proceso cambio el pago. Actualiza y revisa su estado antes de continuar.'})[error.status]
    || 'No se pudo confirmar la respuesta. Revisa la conexion y actualiza el estado; no repitas la decision sin revisarlo.';
}
export const login = (username, password) => request('/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username,password})});
export const staffRequest = (path, token, options = {}) => request(path, {...options, headers:{Accept:'application/json', Authorization:'Bearer '+token, ...(options.method ? {'Content-Type':'application/json'} : {})}});
export async function proofBlob(id, token, {signal, fetchImpl = globalThis.fetch} = {}) {
  let response;
  try { response = await fetchImpl(base()+`/payments/${id}/proof`, {signal, cache:'no-store', headers:{Authorization:'Bearer '+token, Accept:'image/png, image/jpeg'}}); }
  catch { throw new ApiError('No se pudo cargar el comprobante.'); }
  if (!response.ok) throw new ApiError('Comprobante no disponible.', {status:response.status});
  const blob = await response.blob();
  if (!['image/png','image/jpeg'].includes(blob.type)) throw new ApiError('Formato de comprobante invalido.');
  return blob;
}

export async function decidePayment(payment, status, token) {
  const result = await staffRequest(`/payments/${payment.id}/status`, token, {method:'PATCH',body:JSON.stringify({status})});
  if (result?.id !== payment.id || result.order_id !== payment.order_id || result.status !== status)
    throw new ApiError('No se pudo verificar la decision.', {uncertain:true});
  return result;
}
