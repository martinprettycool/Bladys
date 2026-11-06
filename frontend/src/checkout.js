import { element as el, button, state, badge } from './components.js';
import { cents, money, identity } from './cart.js';

export function summaryRows(items, labels = items, confirmed = false) {
  const list = el('div', { className: 'checkout-items' });
  for (const item of items) {
    const id = item.variant_id ?? item.variant.id;
    const label = labels.find(i => identity(i.variant.id, i.selected_flavors) === identity(id, item.selected_flavors)) || labels.find(i => i.variant.id === id);
    const price = confirmed ? cents(item.unit_price) : cents(item.variant.price);
    const copy = el('div', { className: 'checkout-item-copy' }, [
      el('h3', { text: label?.product.name || `Variante ${id}` }),
      el('p', { text: label?.variant.name || `Variante ${id}` }),
      el('p', { className: 'muted', text: `${item.quantity} \u00d7 ${money(price)} c/u` }),
    ]);
    if (item.selected_flavors.length) copy.append(el('p', { className: 'cart-flavors', text: item.selected_flavors.join(' + ') }));
    list.append(el('article', { className: 'checkout-item' }, [copy, el('strong', { text: money(price * item.quantity) })]));
  }
  return list;
}
export function checkoutScreen({ cart, checkout, onEdit, onSuccess }) {
  const node = el('section', { className: 'customer-shell checkout-shell', 'aria-labelledby': 'checkout-title' });
  const unsubscribe = checkout.subscribe(() => {
    node.replaceChildren(el('p', { className: 'eyebrow', text: 'TU CARRITO' }), el('h1', { id: 'checkout-title', text: 'Revisa tus favoritos' }));
    const review = checkout.reviewItems;
    if (!review.length && !checkout.pending) {
      node.append(state({ title: 'Tu carrito est\u00e1 vac\u00edo', description: 'Elige algo del men\u00fa antes de confirmar.', action: el('a', { className: 'button button-primary', href: '#menu', text: 'Ver men\u00fa' }) }));
      return;
    }
    const confirm = button(checkout.busy ? 'Confirmando\u2026' : checkout.confirmedRecovery ? 'Guardar acceso al pedido' : checkout.pending ? 'Reintentar confirmaci\u00f3n' : 'Confirmar pedido', {
      className: 'checkout-confirm', disabled: checkout.busy,
      onClick: async () => { const receipt = await checkout.submit(); if (receipt) onSuccess(); },
    });
    confirm.dataset.confirmOrder = '';
    node.append(summaryRows(review), el('div', { className: 'cart-total' }, [el('strong', { text: 'Total estimado' }), el('strong', { text: money(review.reduce((total, item) => total + cents(item.variant.price) * item.quantity, 0)) })]),
      el('p', { className: 'muted cart-note', text: 'El restaurante confirma los precios y el total al crear el pedido.' }));
    if (checkout.draftDiffers) node.append(el('p', { className: 'checkout-notice', text: 'Se muestra la selecci\u00f3n del intento pendiente. Los cambios posteriores del carrito se conservan aparte. Recupera este intento antes de confirmar una nueva compra.' }));
    if (checkout.storageWarning && checkout.confirmedRecovery) node.append(el('p', { role: 'alert', className: 'checkout-error', text: 'El pedido ya fue creado. No pudimos guardar su acceso; conservamos el carrito y las credenciales en esta pesta\u00f1a. Guardar el acceso no crea otro pedido.' }));
    if (checkout.pending && !checkout.error && !checkout.busy) node.append(el('p', { className: 'checkout-notice', text: 'Hay un intento guardado. Reintentar conserva la misma solicitud para evitar duplicados.' }));
    if (checkout.error) node.append(el('div', { className: 'checkout-error', role: 'alert', tabindex: '-1' }, [
      el('p', { text: checkout.error.message }),
      el('p', { text: checkout.error.uncertain ? 'El pedido pudo haberse creado. Conservamos tu carrito: reintenta sin modificarlo para recuperar el mismo pedido. Los cambios del carrito no reemplazan este intento; primero recuperaremos el pedido original.' : 'Tu carrito se conserva. Puedes revisarlo antes de volver a confirmar.' }),
    ]));
    node.append(el('div', { className: 'checkout-actions', 'aria-busy': String(checkout.busy) }, [confirm,
      button('Volver al carrito', { variant: 'secondary', disabled: checkout.busy, onClick: onEdit })]));
    if (checkout.error) node.querySelector('[role=alert]')?.focus({ preventScroll: true });
  });
  return { node, cleanup: unsubscribe };
}
