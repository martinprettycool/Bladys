import { element as el, button, badge, input, card, state, brand } from './components.js';
import { customerMenu, showCart, subscribeCart, cart, checkout } from './menu.js';

import { checkoutScreen, orderScreen } from './checkout.js';

document.querySelector('[data-brand]').replaceWith(brand());

const main = document.querySelector('main');
const dialog = document.querySelector('dialog');
let routeCleanup = () => {};

function showProduct(name) {
  dialog.replaceChildren(el('h2', { id: 'dialog-title', text: name }), el('p', { text: 'Ejemplo de componente de la guía visual.' }), button('Cerrar', { onClick: () => dialog.close() }));
  dialog.showModal();
}

function staff(role) {
  const reception = role === 'reception';
  const name = reception ? 'Recepción' : 'Cocina';
  const sidebar = el('aside', { className: 'staff-sidebar' }, [
    el('p', { className: 'eyebrow', text: 'Blady’s · Equipo' }),
    el('strong', { text: name }),
    el('nav', { 'aria-label': 'Áreas del equipo' }, [
      el('a', { href: '#reception', text: 'Recepción', ...(reception ? { 'aria-current': 'page' } : {}) }),
      el('a', { href: '#kitchen', text: 'Cocina', ...(!reception ? { 'aria-current': 'page' } : {}) }),
    ]),
  ]);
  const workspace = el('section', { className: 'staff-workspace' }, [
    badge('Vista previa', 'warning'),
    el('h1', { text: reception ? 'Verificación de pagos' : 'Pedidos en cocina' }),
    el('p', { className: 'muted', text: reception ? 'Un espacio claro para revisar comprobantes y resolver pagos.' : 'Un espacio de trabajo enfocado en preparar y despachar pedidos.' }),
    card([state({ title: 'El espacio está preparado', description: 'El acceso del equipo y los pedidos reales se conectarán en la siguiente etapa. Esta pantalla no consulta ni representa una cola operativa.' })]),
  ]);
  return el('div', { className: 'staff-shell' }, [sidebar, workspace]);
}

function foundation() {
  const field = input('Nombre de referencia', { id: 'reference', placeholder: 'Ej. Pedido para compartir' });
  return el('div', { className: 'foundation-shell' }, [
    el('p', { className: 'eyebrow', text: 'Blady’s · Fundamentos' }),
    el('h1', { text: 'Una identidad, tres experiencias.' }),
    el('p', { className: 'muted', text: 'Componentes compartidos para clientes, recepción y cocina.' }),
    el('div', { className: 'section-stack' }, [
      card([el('h2', { text: 'Nuestra marca' }), brand(), el('p', { className: 'muted', text: 'Personaje original con recorte circular, nombre legible y acentos italianos. El archivo original no se modifica.' })]),
      card([el('h2', { text: 'Acciones' }), el('div', { className: 'component-row' }, [
        button('Acción principal', { onClick: () => showProduct('Acción de ejemplo') }),
        button('Acción secundaria', { variant: 'secondary', onClick: () => showProduct('Acción secundaria de ejemplo') }),
        button('No disponible', { disabled: true }),
      ])]),
      card([field.wrapper]),
      card([el('h2', { text: 'Estados' }), el('div', { className: 'component-row' }, [
        badge('Informativo'), badge('Pendiente', 'warning'), badge('Confirmado', 'success'), badge('Requiere atención', 'danger'),
      ])]),
      card([state({ kind: 'loading', title: 'Cargando el menú', description: 'Ejemplo visual de una solicitud en curso.' })]),
      card([state({ title: 'Todavía no hay elementos', description: 'Los elementos aparecerán aquí cuando estén disponibles.' })]),
      state({ kind: 'error', title: 'No pudimos cargar la información', description: 'Ejemplo de error recuperable. Revisa tu conexión e intenta de nuevo.' }),
    ]),
  ]);
}

const headerCart = button('Carrito', { variant: 'secondary', onClick: () => showCart(dialog) });
const myOrder = el('a', { href: '#my-order', text: 'Mi pedido', hidden: '' });
document.querySelector('.site-header nav').replaceChildren(myOrder, headerCart);
checkout.subscribe(() => { myOrder.hidden = !checkout.receipt; });
subscribeCart(() => {
  headerCart.textContent = cart.count ? `Carrito (${cart.count})` : 'Carrito';
  headerCart.classList.toggle('has-items', cart.count > 0);
});

function route() {
  const requested = location.hash.slice(1);
  const customerAnchor = requested === 'menu' || requested === 'pizzas' || requested.startsWith('category-');
  if (customerAnchor && document.body.dataset.surface === 'menu') {
    const target = document.getElementById(requested === 'pizzas' ? 'menu' : requested);
    target?.scrollIntoView();
    if (requested.startsWith('category-')) target?.focus({ preventScroll: true });
    return;
  }
  routeCleanup();
  if (dialog.open) dialog.close();
  const current = ['reception', 'kitchen', 'foundation', 'checkout', 'my-order'].includes(requested) ? requested : 'menu';
  document.body.dataset.surface = current;
  if (current === 'checkout' || current === 'my-order') {
    const view = current === 'checkout' ? checkoutScreen({ cart, checkout, onEdit: () => showCart(dialog), onSuccess: () => { location.hash = 'my-order'; } }) : orderScreen({ checkout });
    main.replaceChildren(view.node);
    routeCleanup = view.cleanup;
  } else if (current === 'menu') {
    const view = customerMenu(dialog);
    main.replaceChildren(view.node);
    routeCleanup = view.cleanup;
  } else {
    main.replaceChildren(current === 'foundation' ? foundation() : staff(current));
    routeCleanup = () => {};
  }
  document.title = "Blady's Pizza · " + ({ menu: 'Menú', reception: 'Recepción', kitchen: 'Cocina', foundation: 'Guía visual', checkout: 'Confirmar carrito', 'my-order': 'Mi pedido' }[current]);
  if (current === 'checkout' || current === 'my-order') window.scrollTo(0, 0);
  if (requested && !customerAnchor) main.focus({ preventScroll: true });
}
window.addEventListener('hashchange', route);
route();
