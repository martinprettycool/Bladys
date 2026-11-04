import { element as el, button, input, state } from './components.js';
import { createCheckout } from './checkout-state.js';
import { createOrder, getProducts } from './api.js';
import { available, cents, money, createCart } from './cart.js';
import { configureProduct, openSheet } from './configurator.js';
import { categoryCard } from './category-card.js';

// Labels translate API enums, never infer categories from product names.
const categoryLabels = { PIZZA: 'Pizzas', HAMBURGUESA: 'Hamburguesas', ALITAS: 'Alitas', PAPITAS: 'Papitas' };
const categoryOrder = Object.keys(categoryLabels);
const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
export const cart = createCart();
export const checkout = createCheckout({ cart, send: createOrder, storage: () => sessionStorage });
const cartListeners = new Set();
checkout.subscribe(() => cartListeners.forEach(listener => listener()));
export function subscribeCart(listener) { cartListeners.add(listener); listener(); return () => cartListeners.delete(listener); }
function changed() { checkout.cartChanged(); cartListeners.forEach(listener => listener()); }
export function showCart(dialog) {
  if (checkout.busy) { dialog.close(); location.hash = 'checkout'; return; }
  const content = el('div', { className: 'cart-content' });
  function render() {
    content.replaceChildren();
    if (!cart.items.length) {
      content.append(state({ title: '¿Qué se te antoja?', description: 'Elige algo del menú para empezar tu carrito.', action: button('Ver menú', { onClick: () => { dialog.close(); location.hash = 'menu'; document.querySelector('#menu')?.scrollIntoView(); } }) }));
      return;
    }
    for (const item of cart.items) {
      const copy = el('div', { className: 'cart-item-copy' }, [el('h3', { text: item.product.name }), el('p', { text: item.variant.name })]);
      if (item.selected_flavors.length) copy.append(el('p', { className: 'cart-flavors', text: item.selected_flavors.join(' + ') }));
      const change = delta => { cart.change(item.key, delta); changed(); render(); };
      content.append(el('article', { className: 'cart-item' }, [copy,
        el('strong', { text: money(cents(item.variant.price) * item.quantity) }),
        el('div', { className: 'quantity-control' }, [
          button('−', { variant: 'secondary', ariaLabel: `Reducir ${item.variant.name} ${item.product.name} ${item.selected_flavors.join(', ')}`, onClick: () => change(-1) }),
          el('output', { text: item.quantity, 'aria-label': 'Cantidad' }),
          button('+', { variant: 'secondary', ariaLabel: `Aumentar ${item.variant.name} ${item.product.name} ${item.selected_flavors.join(', ')}`, onClick: () => change(1) }),
        ]),
        button('Quitar', { variant: 'quiet', ariaLabel: `Quitar ${item.variant.name} ${item.product.name} ${item.selected_flavors.join(', ')}`, onClick: () => change(-item.quantity) }),
      ]));
    }
    content.append(el('div', { className: 'cart-total' }, [el('strong', { text: 'Total estimado' }), el('strong', { text: money(cart.total) })]),
      el('p', { className: 'muted cart-note', text: 'Revisa tu carrito antes de confirmar. El total final lo confirma el restaurante.' }),
      button('Continuar a confirmar', { className: 'checkout-start sheet-submit', onClick: () => { dialog.close(); location.hash = 'checkout'; } }),
      button('Seguir eligiendo', { variant: 'quiet', className: 'sheet-submit', onClick: () => dialog.close() }));
  }
  render();
  openSheet(dialog, 'Carrito', [content]);
}

export function customerMenu(dialog) {
  const controller = new AbortController();
  const shell = el('div', { className: 'customer-shell' });
  shell.append(el('section', { className: 'hero', 'aria-labelledby': 'hero-title' }, [
    el('div', { className: 'hero-content' }, [el('p', { className: 'eyebrow', text: 'BLADY’S · PIZZA Y MÁS' }), el('h1', { id: 'hero-title', text: 'Hoy se comparte algo bueno.' }), el('p', { className: 'hero-copy', text: 'Tu pizza favorita y algo más para todos. Elige tus favoritos.' })]),
    el('a', { className: 'button hero-menu-link', href: '#menu', text: 'Ver menú ↓' }),
  ]));
  const menu = el('section', { id: 'menu', className: 'menu-discovery', 'aria-labelledby': 'menu-title' }, [el('div', { className: 'menu-heading' }, [el('h2', { id: 'menu-title', text: 'Menú' })])]);
  const discovery = el('div');
  const sections = el('div', { className: 'menu-sections' });
  const search = input('Buscar en el menú', { id: 'menu-search', type: 'search', placeholder: 'Busca tu favorito…' });
  search.wrapper.classList.add('search-field');
  const resultCount = el('p', { className: 'result-count muted', role: 'status', 'aria-live': 'polite' });
  menu.append(discovery, search.wrapper, resultCount, sections);
  search.wrapper.hidden = true;
  const dockLabel = el('p');
  const dockButton = button('Ver carrito', { onClick: () => showCart(dialog) });
  const dock = el('aside', { className: 'order-dock', 'aria-label': 'Carrito' }, [el('div', { className: 'order-dock-inner' }, [el('div', {}, [el('strong', { text: 'Carrito' }), dockLabel]), dockButton])]);
  const unsubscribe = subscribeCart(() => { dock.hidden = !cart.count; dockLabel.textContent = `${cart.count} ${cart.count === 1 ? 'producto' : 'productos'} · ${money(cart.total)}`; });
  shell.append(menu, dock);
  const announcement = document.querySelector('#cart-announcement');
  let products = [];
  const groups = () => [...new Set(products.map(product => product.category).filter(Boolean))].sort((a, b) => {
    const ai = categoryOrder.indexOf(a), bi = categoryOrder.indexOf(b);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  function productCard(product) {
    const variants = product.variants.filter(variant => available(product, variant));
    const prices = variants.map(variant => cents(variant.price));
    const canOrder = variants.length > 0;
    const content = el('div', { className: 'item-content' }, [el('h3', { text: product.name })]);
    if (product.description) content.append(el('p', { text: product.description }));
    else if (product.variants.length === 1 && product.variants[0].description) content.append(el('p', { text: product.variants[0].description }));
    content.append(el('strong', { className: 'item-price', text: canOrder ? `${product.variants.length > 1 ? 'Desde ' : ''}${money(Math.min(...prices))}` : 'No disponible' }));
    return el('article', { className: 'menu-item' }, [content, button('Agregar', { variant: 'secondary', className: 'item-action', disabled: !canOrder, ariaLabel: 'Agregar ' + product.name, onClick: () => configureProduct(dialog, product, (p, v, quantity, flavors) => { cart.add(p, v, quantity, flavors); changed(); announcement.textContent = `${quantity} ${p.name}, ${v.name}, agregado al carrito.`; }) })]);
  }
  function renderProducts() {
    const query = normalize(search.control.value.trim());
    const matches = products.filter(p => normalize([p.name, p.description, ...p.variants.map(v => `${v.name} ${v.description || ''}`)].join(' ')).includes(query));
    sections.replaceChildren();
    for (const category of groups()) {
      const categoryProducts = matches.filter(p => p.category === category);
      if (!categoryProducts.length) continue;
      const id = 'category-' + category.toLowerCase();
      sections.append(el('section', { id, className: 'menu-section', 'aria-labelledby': id + '-title', tabindex: '-1' }, [
        el('div', { className: 'section-heading' }, [el('h2', { id: id + '-title', text: categoryLabels[category] || category }), el('a', { href: '#menu', text: 'Categorías ↑', className: 'back-to-menu' })]),
        el('div', { className: 'catalog' }, categoryProducts.map(productCard)),
      ]));
    }
    const uncategorized = matches.filter(p => !p.category);
    if (uncategorized.length) sections.append(el('div', { className: 'catalog' }, uncategorized.map(productCard)));
    resultCount.textContent = query ? `${matches.length} ${matches.length === 1 ? 'resultado' : 'resultados'}` : '';
    if (!matches.length) sections.append(state({ title: 'No encontramos ese antojo', description: 'Prueba otro nombre o vuelve a explorar el menú.', action: button('Limpiar búsqueda', { variant: 'secondary', onClick: () => { search.control.value = ''; renderProducts(); search.control.focus(); } }) }));
  }
  async function load() {
    discovery.replaceChildren(state({ kind: 'loading', title: 'Preparando el menú', description: 'Consultando opciones y precios del restaurante.' }));
    search.wrapper.hidden = true;
    sections.replaceChildren();
    resultCount.textContent = '';
    try {
      products = await getProducts({ signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!products.length) {
        discovery.replaceChildren(state({ title: 'El menú estará de vuelta pronto', description: 'En este momento no hay productos publicados.', action: button('Volver a consultar', { variant: 'secondary', onClick: load }) }));
        return;
      }
      discovery.replaceChildren(el('nav', { className: 'category-grid', 'aria-label': 'Categorías del menú' }, groups().map((category, index) => {
        const categoryProducts = products.filter(p => p.category === category);
        return categoryCard({
          category, index,
          label: categoryLabels[category] || category,
          count: categoryProducts.length,
          onNavigate: () => { if (search.control.value) { search.control.value = ''; renderProducts(); } },
        });
      })));
      discovery.append(el('p', { className: 'category-disclaimer', text: 'Im\u00e1genes ilustrativas. Consulta los ingredientes y variantes de cada producto.' }));
      search.wrapper.hidden = false;
      renderProducts();
      if (location.hash.startsWith('#category-')) document.getElementById(location.hash.slice(1))?.scrollIntoView();
    } catch (error) {
      if (error.name === 'AbortError') return;
      discovery.replaceChildren(state({ kind: 'error', title: 'No pudimos cargar el menú', description: 'Revisa tu conexión e intenta de nuevo. No mostraremos precios desactualizados.', action: button('Reintentar', { onClick: load }) }));
    }
  }
  search.control.addEventListener('input', renderProducts);
  load();
  return { node: shell, cleanup: () => { controller.abort(); unsubscribe(); } };
}
