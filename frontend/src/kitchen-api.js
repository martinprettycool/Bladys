import { validOrder, ApiError } from './api.js';
import { staffRequest } from './reception-api.js';
export const nextStatus = {PENDIENTE_ACEPTACION:'PREPARANDO', PREPARANDO:'LISTO', LISTO:'ENVIADO'};
export const actionLabel = {PENDIENTE_ACEPTACION:'Aceptar y preparar', PREPARANDO:'Marcar LISTO', LISTO:'Marcar ENVIADO'};
export function kitchenMessage(error) {
  return ({401:'Sesion invalida o vencida. Inicia sesion nuevamente.',
    403:'Acceso denegado: se requiere un usuario KITCHEN activo.',
    404:'La ruta de Cocina no esta disponible. Reinicia el proxy frontend y comprueba que el backend este actualizado.',
    502:'El proxy no puede conectar con el backend. Comprueba que FastAPI este en ejecucion.',
    409:'El pedido cambio o la transicion ya no esta permitida. Revisa el estado actualizado.'})[error.status]
    || 'No se pudo confirmar la respuesta. Actualiza el estado antes de intentar otra accion.';
}
export async function kitchenQueue(token) {
  const orders = await staffRequest('/kitchen/orders', token);
  if (!Array.isArray(orders) || orders.some(o => !validOrder(o) || !nextStatus[o.status]) || new Set(orders.map(o=>o.id)).size !== orders.length) throw new ApiError('Cola invalida');
  return orders;
}
export async function advanceOrder(order, token) {
  const status = nextStatus[order.status];
  if (!status) throw new ApiError('Transicion invalida');
  const result = await staffRequest(`/kitchen/orders/${order.id}/status`, token, {method:'PATCH', body:JSON.stringify({status})});
  if (!validOrder(result) || result.id !== order.id || result.status !== status) throw new ApiError('Respuesta no verificable', {uncertain:true});
  return result;
}
export function itemLabel(item, products) {
  for (const product of products) {
    const variant = product.variants?.find(v=>v.id === item.variant_id);
    if (variant) return `${item.quantity} x ${product.name} - ${variant.name}${item.selected_flavors.length ? ' | Sabores: '+item.selected_flavors.join(', ') : ''}`;
  }
  return `${item.quantity} x Variante #${item.variant_id}${item.selected_flavors.length ? ' | Sabores: '+item.selected_flavors.join(', ') : ''}`;
}
