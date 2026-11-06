import { createCart } from './cart.js';
import { getOrder, getOrderPayment, replaceOrder } from './api.js';

export function editingReason(order, payment) {
  if (order.status !== 'PENDIENTE_ACEPTACION') return 'El pedido avanzó o fue cancelado y ya no permite modificaciones.';
  if (!payment) return 'No pudimos verificar el pago. Actualiza antes de editar.';
  if (payment.proof_reference !== null || payment.status !== 'PENDIENTE_COMPROBANTE')
    return 'El pago ya tiene comprobante o ha avanzado. No se puede modificar el pedido.';
  if (!Number.isSafeInteger(order.version) || order.version < 1) return 'No pudimos verificar la versión del pedido.';
  return '';
}

// A draft consumes the version it was created from. Refresh never rebases it.
export function createOrderEditor({ receipt, products, read = getOrder, readPayment = getOrderPayment, put = replaceOrder, onUpdate = () => {} }) {
  const draft = createCart();
  for (const item of receipt.order.items) {
    const product = products.find(p => p.variants.some(v => v.id === item.variant_id));
    const variant = product?.variants.find(v => v.id === item.variant_id);
    // Preserve historical/unavailable lines for explicit removal/replacement.
    const p = product || { name: `Variante ${item.variant_id}`, is_available: false, flavor_options: [] };
    const v = variant || { id: item.variant_id, name: 'No disponible', price: item.unit_price, is_available: false };
    draft.items.push({ key: JSON.stringify([v.id, [...item.selected_flavors].sort()]), product: p, variant: v,
      quantity: item.quantity, selected_flavors: [...item.selected_flavors] });
  }
  const expectedVersion = receipt.order.version;
  let busy = false, blocked = false, message = '', current = receipt.order, payment = null;
  return {
    draft, expectedVersion,
    get busy() { return busy; }, get blocked() { return blocked; }, get message() { return message; },
    get current() { return current; }, get payment() { return payment; },
    async refresh() {
      // This deliberately does not change expectedVersion or draft.
      try {
        current = await read(receipt.order.id, receipt.token);
        onUpdate(current);
        payment = null;
        payment = await readPayment(receipt.order.id, receipt.token);
        if (current.version !== expectedVersion || editingReason(current, payment)) blocked = true;
        return current;
      } catch (error) {
        blocked = true;
        throw error;
      }
    },
    async save() {
      if (busy || blocked || !draft.count) return false;
      busy = true; message = '';
      try {
        const result = await put(receipt.order.id, receipt.token, { expected_version: expectedVersion, ...draft.payload() });
        current = result; blocked = true;
        onUpdate(result, draft.items);
        return true;
      } catch (error) {
        if (error.status === 409 || error.uncertain || [401, 403, 404].includes(error.status)) blocked = true;
        message = error.code === 'ORDER_VERSION_CONFLICT'
          ? 'El pedido cambió en otra operación. Tu borrador se conserva; actualiza para revisar el pedido actual. No se ha reenviado.'
          : error.uncertain ? 'No pudimos confirmar si se guardaron los cambios. Consultaremos el pedido actual; no volveremos a enviar el borrador automáticamente.'
          : [401, 403].includes(error.status) ? 'El acceso al pedido no es válido o expiró. Contacta al restaurante.'
          : error.status === 409 ? 'El pedido o su pago ya no permiten esta modificación. Actualiza para revisar su estado.' : error.message;
        if (error.uncertain) {
          try { await this.refresh(); message += ' Se consultó el estado actual. Revísalo antes de iniciar otra edición.'; }
          catch { message += ' No pudimos consultar el estado actual. Actualiza antes de continuar.'; }
        }
        return false;
      } finally { busy = false; }
    },
  };
}
