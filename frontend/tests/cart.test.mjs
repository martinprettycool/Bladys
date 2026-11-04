import test from 'node:test';
import assert from 'node:assert/strict';
import { createCart, cents, available } from '../src/cart.js';
const product = { id: 1, name: 'Menu item', is_available: true, flavor_options: ['BBQ', 'Buffalo', 'Other'] };
const variant = { id: 10, name: 'Option', is_available: true, price: '7.50', max_flavors: 2 };
test('cart identity keeps different flavors separate and merges reordered identical sets', () => {
  const cart = createCart();
  cart.add(product, variant, 1, ['BBQ', 'Buffalo']);
  cart.add(product, variant, 2, ['Buffalo', 'BBQ']);
  cart.add(product, variant, 1, ['Other']);
  assert.equal(cart.items.length, 2);
  assert.equal(cart.items[0].quantity, 3);
  assert.equal(cart.total, 3000);
  assert.deepEqual(cart.payload(), { items: [
    { variant_id: 10, quantity: 3, selected_flavors: ['BBQ', 'Buffalo'] },
    { variant_id: 10, quantity: 1, selected_flavors: ['Other'] },
  ] });
  cart.change(cart.items[1].key, -1);
  assert.equal(cart.items.length, 1);
});
test('unavailable and invalid flavor selections cannot enter cart', () => {
  const cart = createCart();
  assert.equal(available(product, { ...variant, is_available: false }), false);
  for (const flavors of [[], ['BBQ', 'BBQ'], ['Invalid'], ['BBQ', 'Buffalo', 'Other']]) {
    assert.throws(() => cart.add(product, variant, 1, flavors));
  }
  assert.throws(() => cart.add({ ...product, is_available: false }, variant, 1, ['BBQ']));
  assert.throws(() => cart.add(product, variant, 0, ['BBQ']));
  assert.throws(() => cart.add(product, { ...variant, max_flavors: null }, 1, ['BBQ']));
  cart.add(product, { ...variant, max_flavors: null }, 1);
  assert.equal(cart.count, 1);
});
test('display arithmetic uses cents and rejects malformed prices', () => {
  assert.equal(cents('3.4'), 340);
  assert.equal(cents('2.70'), 270);
  assert.equal(cents('0'), 0);
  for (const value of ['NaN', '-1.00', '1.005', null]) assert.equal(cents(value), null);
});
