import { element as el, button } from './components.js';
import { available, cents, money } from './cart.js';

export function openSheet(dialog, title, children) {
  dialog.replaceChildren(
    el('div', { className: 'sheet-header' }, [el('h2', { id: 'dialog-title', text: title }), button('Cerrar ×', { variant: 'quiet', ariaLabel: 'Cerrar ventana', onClick: () => dialog.close() })]),
    ...children,
  );
  if (!dialog.open) dialog.showModal();
  dialog.scrollTop = 0;
}

export function configureProduct(dialog, product, onAdd) {
  let selected = product.variants.length === 1 && available(product, product.variants[0]) ? product.variants[0] : null;
  let quantity = 1;
  let flavors = [];
  const flavorArea = el('div');
  const variantSet = el('fieldset', { className: 'choice-set' }, [el('legend', { text: 'Elige una opción' })]);
  const quantityLabel = el('output', { text: '1', 'aria-live': 'polite', 'aria-label': 'Cantidad' });
  const minus = button('−', { variant: 'secondary', ariaLabel: 'Reducir cantidad', disabled: true, onClick: () => { quantity--; update(); } });
  const plus = button('+', { variant: 'secondary', ariaLabel: 'Aumentar cantidad', onClick: () => { quantity++; update(); } });
  const add = button('Elige una opción', { disabled: true, className: 'sheet-submit', onClick: () => {
    onAdd(product, selected, quantity, flavors);
    dialog.close();
  } });
  function update() {
    quantityLabel.textContent = quantity;
    minus.disabled = quantity === 1;
    add.disabled = !selected || !available(product, selected) || (selected.max_flavors > 0 && flavors.length === 0);
    add.textContent = selected ? `Agregar al carrito · ${money(cents(selected.price) * quantity)}` : 'Elige una opción';
  }
  function renderFlavors() {
    flavorArea.replaceChildren();
    if (!(selected?.max_flavors > 0)) return;
    const limit = selected.max_flavors;
    const counter = el('p', { className: 'flavor-count', role: 'status', 'aria-live': 'polite', text: `0 de ${limit} sabores · Elige al menos 1` });
    const fieldset = el('fieldset', { className: 'choice-set flavor-set' }, [el('legend', { text: `Elige hasta ${limit} ${limit === 1 ? 'sabor' : 'sabores'}` }), counter]);
    const controls = [];
    for (const flavor of product.flavor_options || []) {
      const checkbox = el('input', { type: 'checkbox', value: flavor });
      controls.push(checkbox);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && flavors.length < limit) flavors.push(flavor);
        else flavors = flavors.filter(value => value !== flavor);
        for (const control of controls) {
          control.checked = flavors.includes(control.value);
          control.disabled = !control.checked && flavors.length >= limit;
        }
        counter.textContent = `${flavors.length} de ${limit} sabores · Elige al menos 1`;
        update();
      });
      fieldset.append(el('label', { className: 'choice flavor-choice' }, [checkbox, el('span', { text: flavor })]));
    }
    flavorArea.append(fieldset);
  }
  for (const variant of product.variants) {
    const radio = el('input', { type: 'radio', name: 'variant', value: variant.id });
    radio.disabled = !available(product, variant);
    radio.checked = selected === variant;
    radio.addEventListener('change', () => { selected = variant; flavors = []; renderFlavors(); update(); });
    const copy = el('span', { className: 'choice-copy' }, [el('strong', { text: variant.name })]);
    if (variant.description) copy.append(el('span', { className: 'muted', text: variant.description }));
    if (radio.disabled) copy.append(el('span', { className: 'muted', text: 'No disponible' }));
    variantSet.append(el('label', { className: 'choice' }, [radio, copy, el('strong', { className: 'choice-price', text: cents(variant.price) === null ? '—' : money(cents(variant.price)) })]));
  }
  renderFlavors();
  update();
  openSheet(dialog, product.name, [
    ...(product.description ? [el('p', { className: 'config-description', text: product.description })] : []),
    variantSet, flavorArea,
    el('div', { className: 'quantity-row' }, [el('strong', { text: 'Cantidad' }), el('div', { className: 'quantity-control' }, [minus, quantityLabel, plus])]),
    add,
  ]);
}
