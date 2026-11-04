// Dependency-free Chromium/CDP integration review. All servers and browser are isolated.
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const processes = [];
function start(command, args) {
  const process = spawn(command, args, { windowsHide: true, stdio: 'ignore' });
  processes.push(process);
  return process;
}
async function ready(url) {
  for (let i = 0; i < 100; i++) {
    try { const response = await fetch(url); if (response.ok) return response; } catch {}
    await wait(100);
  }
  throw new Error(`Not ready: ${url}`);
}
const output = resolve('frontend/review');
let socket;
try {
  await mkdir(output, { recursive: true });
  start(resolve('.venv/Scripts/python.exe'), ['frontend/tests/catalog_server.py']);
  await ready('http://127.0.0.1:8174/products');
  start('python', ['frontend/serve.py', '--port', '5179', '--backend', 'http://127.0.0.1:8174']);
  const catalog = await (await ready('http://127.0.0.1:5179/products')).json();
  assert.equal(catalog.length, 11);
  const profile = await mkdtemp(join(tmpdir(), 'bladys-review-'));
  start('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', '--disable-gpu', '--no-first-run', '--remote-debugging-port=9234', `--user-data-dir=${profile}`, 'about:blank']);
  const targets = await (await ready('http://127.0.0.1:9234/json')).json();
  socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }));
  let next = 0;
  const pending = new Map();
  let mode = 'real';
  let checkoutMode = 'real';
  const orderKeys = [];
  const errors = [];
  function send(method, params = {}) {
    const id = ++next;
    return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  }
  socket.addEventListener('message', async event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const handler = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) handler.reject(message.error); else handler.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    else if (message.method === 'Fetch.requestPaused') {
      const requestId = message.params.requestId;
      if (new URL(message.params.request.url).pathname === '/orders') {
        const responseStage = message.params.responseStatusCode !== undefined;
        if (!responseStage) orderKeys.push(message.params.request.headers['Idempotency-Key']);
        if (checkoutMode === 'reject' && !responseStage) {
          await send('Fetch.fulfillRequest', { requestId, responseCode: 422, responseHeaders: [{name:'Content-Type',value:'application/json'}], body: Buffer.from(JSON.stringify({detail:'Revisa los sabores'})).toString('base64') });
        } else if (checkoutMode === 'lose-response' && responseStage) {
          checkoutMode = 'real';
          await send('Fetch.failRequest', { requestId, errorReason: 'ConnectionClosed' });
        } else await send('Fetch.continueRequest', { requestId });
      } else if (mode === 'real') await send('Fetch.continueRequest', { requestId });
      else if (mode === 'loading') { /* Leave request pending until next navigation. */ }
      else {
        let body = mode === 'empty' ? [] : catalog.map(p => ({ ...p, is_available: false }));
        await send('Fetch.fulfillRequest', { requestId, responseCode: mode === 'error' ? 503 : 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(body)).toString('base64') });
      }
    }
  });
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Fetch.enable', { patterns: [{ urlPattern: '*/products' }, { urlPattern: '*/orders', requestStage: 'Request' }, { urlPattern: '*/orders', requestStage: 'Response' }] });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  async function until(expression) {
    for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await wait(50); }
    throw new Error('Condition failed: ' + expression);
  }
  const shot = async name => {
    const result = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(join(output, name + '.png'), Buffer.from(result.data, 'base64'));
  };
  await send('Page.navigate', { url: 'http://127.0.0.1:5179/' });
  await until(`!!document.querySelector('.customer-shell')`);
  await evaluate(`sessionStorage.clear()`);
  for (const [width, height] of [[360,800],[390,844],[430,932],[768,1024],[1440,1000]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 768 });
    await send('Page.navigate', { url: 'http://127.0.0.1:5179/' });
    await until('document.querySelectorAll(".category-card").length === 4');
    await until(`Array.from(document.querySelectorAll('.category-image')).every(image => image.complete && image.naturalWidth > 0)`);
    assert.equal(await evaluate(`document.querySelectorAll('.category-image').length`), 4);
    const layout = await evaluate(`(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, columns: getComputedStyle(document.querySelector('.category-grid')).gridTemplateColumns.split(' ').length, header: document.querySelector('.site-header').getBoundingClientRect().height, hero: document.querySelector('.hero').getBoundingClientRect().height, products: document.querySelectorAll('.menu-item').length }))()`);
    assert.ok(layout.scroll <= layout.width, `Overflow at ${width}`);
    assert.equal(layout.columns, width < 768 ? 2 : 4);
    assert.equal(layout.products, 11);
    await shot(`menu-${width}`);
    console.log('PASS viewport', width, JSON.stringify(layout));
  }
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await send('Page.navigate', { url: 'http://127.0.0.1:5179/' });
  await until(`document.querySelectorAll('.category-image').length === 4 && Array.from(document.querySelectorAll('.category-image')).every(image => image.complete && image.naturalWidth > 0)`);
  await evaluate(`const broken = document.querySelector('.category-image'); broken.removeAttribute('srcset'); broken.src='/missing-category-test.jpg'`);
  await until(`document.querySelector('.category-media').classList.contains('is-placeholder')`);
  assert.equal(await evaluate(`document.querySelector('.category-media').classList.contains('is-placeholder') && !document.querySelector('.category-media').querySelector('img')`), true);
  await evaluate(`document.querySelector('#menu-search').value='zzzznomatch'; document.querySelector('#menu-search').dispatchEvent(new Event('input'))`);
  assert.equal(await evaluate(`document.querySelectorAll('.menu-item').length`), 0);
  await evaluate(`document.querySelector('.category-card').click()`);
  await until(`document.querySelectorAll('.menu-item').length === 11`);
  await evaluate(`document.querySelector('#category-pizza button').click()`);
  assert.equal(await evaluate(`document.querySelector('.sheet-submit').disabled`), true);
  await evaluate(`document.querySelector('input[name=variant]').click()`);
  assert.equal(await evaluate(`document.querySelector('.sheet-submit').disabled`), false);
  await shot('pizza-configurator-390');
  await evaluate(`document.querySelector('.sheet-submit').click()`);
  await evaluate(`document.querySelector('#category-alitas button').click(); document.querySelectorAll('input[name=variant]')[2].click()`);
  assert.equal(await evaluate(`document.querySelector('.sheet-submit').disabled`), true);
  await evaluate(`document.querySelectorAll('input[type=checkbox]')[0].click(); document.querySelectorAll('input[type=checkbox]')[3].click()`);
  assert.equal(await evaluate(`document.querySelectorAll('input[type=checkbox]:disabled').length`), 4);
  assert.equal(await evaluate(`document.querySelector('.flavor-count').textContent.startsWith('2 de 2')`), true);
  await evaluate(`document.querySelector('.flavor-set').scrollIntoView({block: 'start'})`);
  await shot('flavors-390');
  // Changing variants clears incompatible flavors and applies the new limit.
  await evaluate(`document.querySelectorAll('input[name=variant]')[0].click()`);
  assert.equal(await evaluate(`document.querySelectorAll('input[type=checkbox]:checked').length`), 0);
  assert.equal(await evaluate(`document.querySelector('.sheet-submit').disabled`), true);
  await evaluate(`document.querySelector('input[type=checkbox]').click()`);
  assert.equal(await evaluate(`document.querySelectorAll('input[type=checkbox]:disabled').length`), 5);
  await evaluate(`document.querySelectorAll('input[name=variant]')[2].click(); document.querySelectorAll('input[type=checkbox]')[0].click(); document.querySelectorAll('input[type=checkbox]')[3].click()`);
  await evaluate(`document.querySelector('.sheet-submit').click(); document.querySelector('#category-alitas button').click(); document.querySelectorAll('input[name=variant]')[2].click(); document.querySelectorAll('input[type=checkbox]')[5].click(); document.querySelector('.sheet-submit').click(); document.querySelector('.site-header button').focus(); document.querySelector('.site-header button').click()`);
  assert.equal(await evaluate(`document.querySelectorAll('.cart-item').length`), 3);
  assert.equal(await evaluate(`document.querySelectorAll('.cart-flavors').length`), 2);
  assert.equal(await evaluate(`document.querySelector('dialog').scrollWidth <= document.querySelector('dialog').clientWidth`), true);
  await shot('cart-390');
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await until(`!document.querySelector('dialog').open`);
  assert.equal(await evaluate(`document.activeElement === document.querySelector('.site-header button')`), true);
  await evaluate(`document.querySelector('dialog').close(); location.hash='reception'`);
  await until(`!!document.querySelector('.staff-shell')`);
  await evaluate(`location.hash='menu'`);
  await until(`document.querySelectorAll('.category-card').length === 4`);
  assert.equal(await evaluate(`document.querySelector('.site-header button').textContent.includes('(3)')`), true);
  await evaluate(`document.querySelector('.category-card').click()`);
  await until(`location.hash === '#category-pizza'`);
  assert.equal(await evaluate(`document.activeElement.id`), 'category-pizza');
  await evaluate(`document.querySelector('.site-header button').click(); document.querySelector('.checkout-start').click()`);
  await until(`!!document.querySelector('[data-confirm-order]')`);
  assert.equal(await evaluate(`document.querySelectorAll('.checkout-item').length`), 3);
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true);
  await shot('checkout-390');
  // Contexto: el cliente confirma alitas y pizza con dos clics rapidos.
  // Garantia: la confirmacion usa el pedido persistido, sus sabores y su total real.
  // Escenarios: doble clic, respuesta de FastAPI y recarga con acceso de invitado.
  const beforeCheckout = await evaluate(`JSON.parse(sessionStorage.getItem('bladys.guest-checkout.v1')).cart.map(i => ({variant_id:i.variant.id,quantity:i.quantity,selected_flavors:i.selected_flavors}))`);
  checkoutMode = 'reject';
  await evaluate(`document.querySelector('[data-confirm-order]').click()`);
  await until(`!!document.querySelector('.checkout-error')`);
  assert.equal(await evaluate(`document.querySelectorAll('.checkout-item').length`), 3);
  assert.equal(await evaluate(`JSON.parse(sessionStorage.getItem('bladys.guest-checkout.v1')).receipt`), null);
  checkoutMode = 'lose-response';
  await evaluate(`const confirm = document.querySelector('[data-confirm-order]'); confirm.click(); confirm.click()`);
  await until(`!!document.querySelector('.checkout-error') && document.querySelector('.checkout-error').textContent.includes('pudo haberse creado')`);
  assert.equal(await evaluate(`document.querySelectorAll('.checkout-item').length`), 3);
  await shot('checkout-uncertain-390');
  await send('Page.reload');
  await until(`!!document.querySelector('[data-confirm-order]')`);
  await evaluate(`document.querySelector('[data-confirm-order]').click()`);
  await until(`location.hash === '#my-order' && !!document.querySelector('.confirmed-total')`);
  const confirmed = await evaluate(`(async () => {
    const saved = JSON.parse(sessionStorage.getItem('bladys.guest-checkout.v1'));
    const response = await fetch('/orders/' + saved.receipt.order.id, {headers:{'X-Order-Access-Token':saved.receipt.token}});
    const order = await response.json();
    const {money,cents} = await import('/src/cart.js');
    return {id:order.id, items:order.items, status:response.status, empty:saved.cart.length === 0,
      idShown:document.querySelector('.order-number').textContent === 'Pedido #' + order.id,
      totalShown:document.querySelector('.confirmed-total').textContent === money(cents(order.total)),
      tokenVisible:document.body.textContent.includes(saved.receipt.token) || location.href.includes(saved.receipt.token)};
  })()`);
  assert.equal(orderKeys.length, 3);
  assert.ok(orderKeys[0]);
  assert.equal(new Set(orderKeys).size, 1);
  assert.equal(confirmed.status, 200); assert.equal(confirmed.empty, true);
  assert.equal(confirmed.idShown, true); assert.equal(confirmed.totalShown, true); assert.equal(confirmed.tokenVisible, false);
  assert.deepEqual(confirmed.items.map(({variant_id,quantity,selected_flavors}) => ({variant_id,quantity,selected_flavors})), beforeCheckout);
  await shot('order-confirmation-390');
  await send('Page.reload');
  await until(`!!document.querySelector('.confirmed-total')`);
  assert.equal(await evaluate(`document.querySelector('.order-number').textContent`), 'Pedido #' + confirmed.id);
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true);
  await until(`!document.querySelector('.checkout-actions button').disabled`);
  assert.equal(await evaluate(`!!document.querySelector('.checkout-error')`), false);
  console.log('PASS actual POST /orders, selected flavors, server total/ID, guest access header and reload');
  for (const state of ['empty','error','loading','unavailable']) {
    mode = state;
    await send('Page.navigate', { url: 'http://127.0.0.1:5179/' });
    await until(state === 'unavailable' ? `document.querySelectorAll('.menu-item button:disabled').length === 11` : `!!document.querySelector('.state-${state}')`);
    assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true);
    await shot(state + '-390');
    console.log('PASS state', state);
    if (state === 'error') {
      mode = 'real';
      await evaluate(`document.querySelector('.state-error button').click()`);
      await until(`document.querySelectorAll('.category-card').length === 4`);
    }
  }
  assert.deepEqual(errors, []);
  console.log('PASS real API, explicit variants, flavor minimum/maximum, separate cart flavors, route persistence, category keyboard focus; no browser exceptions');
} finally {
  socket?.close();
  for (const process of processes.reverse()) process.kill();
}
