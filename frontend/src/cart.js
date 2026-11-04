// Money stays in integer cents for display; the server owns final pricing.
export const cents = value => {
  const match = String(value).match(/^(\d+)(?:\.(\d{1,2}))?$/);
  return match ? Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0')) : null;
};
export const money = value => new Intl.NumberFormat('es-EC', { style: 'currency', currency: 'USD' }).format(value / 100);
export const available = (product, variant) => product.is_available === true && variant.is_available === true && cents(variant.price) !== null;
export const identity = (variantId, flavors) => JSON.stringify([variantId, [...flavors].sort()]);
export function createCart() {
  const items = [];
  return {
    items,
    add(product, variant, quantity, flavors = []) {
      const chosen = [...flavors].sort();
      const flavorLimit = variant.max_flavors;
      if (!available(product, variant) || !Number.isSafeInteger(quantity) || quantity < 1) throw new Error('Selección no disponible');
      if (new Set(chosen).size !== chosen.length || chosen.some(f => !product.flavor_options.includes(f))) throw new Error('Sabores inválidos');
      if (flavorLimit > 0 ? chosen.length < 1 || chosen.length > flavorLimit : chosen.length > 0) throw new Error('Revisa los sabores');
      const key = identity(variant.id, chosen);
      const existing = items.find(item => item.key === key);
      if (existing) existing.quantity += quantity;
      else items.push({ key, product, variant, quantity, selected_flavors: chosen });
    },
    change(key, delta) {
      const index = items.findIndex(item => item.key === key);
      if (index < 0) return;
      items[index].quantity += delta;
      if (items[index].quantity <= 0) items.splice(index, 1);
    },
    clear() { items.splice(0); },
    get count() { return items.reduce((sum, item) => sum + item.quantity, 0); },
    get total() { return items.reduce((sum, item) => sum + cents(item.variant.price) * item.quantity, 0); },
    // Ready for OrderCreate: never send client prices or totals.
    payload() { return { items: items.map(item => ({ variant_id: item.variant.id, quantity: item.quantity, selected_flavors: [...item.selected_flavors] })) }; },
  };
}
