/** Contexto: cocina procesa un pedido real aprobado desde una pantalla movil.
 * Garantia: solo confirma estados del servidor y evita duplicar envios.
 * Escenarios: rol incorrecto, doble clic, desconexion, recuperacion y recarga.
 * API y proxy reales; SQLite desechable, sin datos del restaurante.
 */
import { spawn } from 'node:child_process';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
const processes = []; let socket;
const wait = ms => new Promise(r=>setTimeout(r,ms));
function start(command,args) {const p = spawn(command,args,{windowsHide:true,stdio:'ignore'}); processes.push(p);}
async function ready(url) {for(let i=0;i<150;i++){try {const r=await fetch(url); if(r.ok)return r;}catch{} await wait(100);} throw new Error('Server unavailable');}
try {
  start(resolve('.venv/Scripts/python.exe'),['frontend/tests/reception_server.py']);
  const products = await (await ready('http://127.0.0.1:8176/products')).json();
  start(resolve('.venv/Scripts/python.exe'),['frontend/serve.py','--port','5181','--backend','http://127.0.0.1:8176']);
  await ready('http://127.0.0.1:5181/');
  const origin = 'http://127.0.0.1:5181';
  const variant = products.find(p=>p.category !== 'ALITAS').variants[0];
  const wing = products.find(p=>p.category === 'ALITAS');
  const wingVariant = wing.variants.find(v=>v.max_flavors >= 2);
  const orders = [];
  for (let i=0;i<2;i++) {
    const response = await fetch(origin+'/orders',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':'reception-browser-'+i},body:JSON.stringify({items:[{variant_id:variant.id,quantity:2},{variant_id:wingVariant.id,quantity:1,selected_flavors:['BBQ','Buffalo']}]})});
    assert.equal(response.status,201); const order = await response.json(); orders.push(order);
    const form = new FormData(); form.append('expected_version',String(order.version));
    form.append('file',new Blob([Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')],{type:'image/png'}),'proof.png');
    assert.equal((await fetch(origin+`/orders/${order.id}/payment/proof`,{method:'POST',headers:{'X-Order-Access-Token':order.order_access_token},body:form})).status,200);
  }
  const reception = await (await fetch(origin+'/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'RECEPTION',password:'browser-test-password'})})).json();
  const rh = {Authorization:'Bearer '+reception.access_token,'Content-Type':'application/json'};
  const payments = await (await fetch(origin+'/payments/pending',{headers:rh})).json();
  assert.equal((await fetch(origin+`/payments/${payments[0].id}/status`,{method:'PATCH',headers:rh,body:JSON.stringify({status:'APROBADO'})})).status,200);
  const profile = await mkdtemp(join(tmpdir(),'bladys-reception-chrome-'));
  start('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-first-run','--remote-debugging-port=9236',`--user-data-dir=${profile}`,'about:blank']);
  const targets = await (await ready('http://127.0.0.1:9236/json')).json();
  socket = new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise(r=>socket.addEventListener('open',r,{once:true}));
  let next=0; const pending = new Map(); const errors = []; let confirms = 0, accept = true;
  const send = (method,params={})=>new Promise((resolve,reject)=>{const id=++next; pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  socket.addEventListener('message',async event=>{
    const message = JSON.parse(event.data);
    if(message.id){const h=pending.get(message.id);pending.delete(message.id);message.error?h.reject(message.error):h.resolve(message.result);}
    else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.text);
    else if(message.method==='Page.javascriptDialogOpening'){confirms++;await send('Page.handleJavaScriptDialog',{accept});}
  });
  await send('Runtime.enable'); await send('Page.enable');
  const evaluate = async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  async function until(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await wait(50);}throw new Error('Condition failed: '+expression+' | '+await evaluate('document.body.innerText')+' | '+JSON.stringify(errors));}
  const click = label=>evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent===${JSON.stringify(label)}).click()`);
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await send('Page.navigate',{url:origin+'/#kitchen'});
  await until(`!!document.querySelector('#kitchen-user')`);
  assert.equal(await evaluate(`document.querySelectorAll('[data-order-id]').length`),0);
  async function loginAs(role) {
    await evaluate(`document.querySelector('#kitchen-user').value=${JSON.stringify(role)};document.querySelector('#kitchen-password').value='browser-test-password';document.querySelector('form').requestSubmit()`);
  }
  await loginAs('RECEPTION'); await until(`document.body.textContent.includes('Acceso denegado')`);
  await loginAs('KITCHEN'); await until(`document.querySelectorAll('[data-order-id]').length===1`);
  const content = await evaluate('document.body.innerText');
  assert.ok(content.includes(wing.name));assert.ok(content.includes(wingVariant.name));assert.ok(content.includes('BBQ, Buffalo'));assert.ok(content.includes('2 x'));
  assert.ok(!content.includes('Aprobar pago'));
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
  await send('Emulation.setDeviceMetricsOverride',{width:768,height:1024,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
  await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate(`globalThis.patchCount=0;globalThis.originalFetch=window.fetch;window.fetch=(...args)=>{if(args[1]?.method==='PATCH')patchCount++;return originalFetch(...args);}`);
  await evaluate(`const action=[...document.querySelectorAll('button')].find(b=>b.textContent==='Aceptar y preparar');action.click();action.click()`);
  await until(`document.body.textContent.includes('Servidor confirmo') && document.body.textContent.includes('Marcar LISTO')`);
  assert.equal(await evaluate('patchCount'),1);
  await evaluate(`document.querySelector('#kitchen-filter').value='PREPARANDO';document.querySelector('#kitchen-filter').dispatchEvent(new Event('change'))`);
  await click('Actualizar cola'); await until(`!document.querySelector('#kitchen-filter').disabled`);
  assert.equal(await evaluate(`document.querySelector('#kitchen-filter').value`),'PREPARANDO');
  // A failed refresh retains the order and blocks transitions until recovery.
  await evaluate(`window.fetch=()=>Promise.reject(Error('offline'))`);await click('Actualizar cola');
  await until(`document.body.textContent.includes('acciones bloqueadas')`);
  assert.equal(await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='Marcar LISTO').disabled`),true);
  await evaluate(`window.fetch=originalFetch`);await click('Actualizar cola');await until(`!document.querySelector('#kitchen-filter').disabled`);
  await click('Marcar LISTO');await until(`document.body.textContent.includes(': LISTO.')`);
  await evaluate(`document.querySelector('#kitchen-filter').value='TODOS';document.querySelector('#kitchen-filter').dispatchEvent(new Event('change'))`);
  await click('Marcar ENVIADO');await until(`document.body.textContent.includes(': ENVIADO.') && document.querySelectorAll('[data-order-id]').length===0`);
  const current = await(await fetch(origin+`/orders/${orders[0].id}`,{headers:{'X-Order-Access-Token':orders[0].order_access_token}})).json();assert.equal(current.status,'ENVIADO');
  await send('Page.reload');await until(`!!document.querySelector('#kitchen-user')`);
  assert.deepEqual(errors,[]);
  console.log('PASS Kitchen Chrome: guest gate, reception denied, real labels/flavors, approved-only queue, duplicate click, full lifecycle, retained filter, offline blocking, mobile width, reload authentication');
} finally {socket?.close();for(const p of processes.reverse())p.kill();}
