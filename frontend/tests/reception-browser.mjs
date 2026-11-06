// Actual UI + proxy + FastAPI; disposable SQLite, no restaurant data.
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
  const orders = [];
  for (let i=0;i<2;i++) {
    const response = await fetch(origin+'/orders',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':'reception-browser-'+i},body:JSON.stringify({items:[{variant_id:variant.id,quantity:2}]})});
    assert.equal(response.status,201); const order = await response.json(); orders.push(order);
    const form = new FormData(); form.append('expected_version',String(order.version));
    form.append('file',new Blob([Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')],{type:'image/png'}),'proof.png');
    assert.equal((await fetch(origin+`/orders/${order.id}/payment/proof`,{method:'POST',headers:{'X-Order-Access-Token':order.order_access_token},body:form})).status,200);
  }
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
  await send('Page.navigate',{url:origin+'/#reception'});
  await until(`!!document.querySelector('#staff-user')`);
  assert.equal(await evaluate(`document.querySelectorAll('.card').length`),0);
  async function signIn(role){await evaluate(`document.querySelector('#staff-user').value=${JSON.stringify(role)};document.querySelector('#staff-password').value='browser-test-password';document.querySelector('form').requestSubmit()`);}
  await signIn('KITCHEN'); await until(`document.body.textContent.includes('no tiene permiso')`);
  assert.equal(await evaluate(`document.querySelectorAll('.card').length`),0);
  await click('Cerrar sesion'); await signIn('RECEPTION');
  await until(`document.querySelectorAll('.card').length===2`);
  assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`),true);
  await evaluate(`window.proofUrls = new Set(); window.decisionRequests = 0; const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL); URL.createObjectURL = blob => { const url = create(blob); proofUrls.add(url); return url; }; URL.revokeObjectURL = url => { proofUrls.delete(url); revoke(url); }; const originalFetch = fetch; window.fetch = (...args) => { if (String(args[0]).startsWith('/payments/') && String(args[0]).endsWith('/status')) window.decisionRequests++; return originalFetch(...args); };`);
  await click('Aprobar pago'); await until(`document.body.textContent.includes('Abre y revisa')`); assert.equal(confirms,0);
  await click('Ver comprobante'); await until(`document.body.textContent.includes('Comprobante cargado')`);
  accept=false; await click('Aprobar pago'); await wait(100); assert.equal(await evaluate(`document.querySelectorAll('.card').length`),2);
  assert.equal(await evaluate('proofUrls.size'),1);
  accept=true; await evaluate(`const approve = [...document.querySelectorAll('button')].find(b=>b.textContent==='Aprobar pago'); approve.click(); approve.click();`); await until(`document.querySelectorAll('.card').length===1`);
  assert.equal(await evaluate('proofUrls.size'),0);
  assert.equal(await evaluate('decisionRequests'),1);
  await click('Ver comprobante'); await until(`document.body.textContent.includes('Comprobante cargado')`);
  await click('Rechazar pago y cancelar pedido'); await until(`document.body.textContent.includes('No hay pagos por verificar')`);
  assert.equal(confirms,3);
  assert.equal(await evaluate('proofUrls.size'),0);
  assert.equal(await evaluate('decisionRequests'),2);
  for(let i=0;i<2;i++){
    const order=orders[i];const headers={'X-Order-Access-Token':order.order_access_token};
    const payment=await(await fetch(origin+`/orders/${order.id}/payment`,{headers})).json();assert.equal(payment.status,i===0?'APROBADO':'RECHAZADO');
    const current=await(await fetch(origin+`/orders/${order.id}`,{headers})).json();assert.equal(current.total,order.total);assert.equal(current.status,i===0?'PENDIENTE_ACEPTACION':'CANCELADO');
  }
  await send('Page.reload'); await until(`!!document.querySelector('#staff-user')`);
  assert.deepEqual(errors,[]);
  console.log('PASS Chrome: guest gate, KITCHEN denial, RECEPTION login, protected image, inspect/confirm, approval, rejection, authoritative refresh, mobile width, JWT lost on reload');
} finally {socket?.close();for(const p of processes.reverse())p.kill();}
