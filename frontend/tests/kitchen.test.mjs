import test from 'node:test';
import assert from 'node:assert/strict';
import { advanceOrder, kitchenQueue, kitchenMessage, itemLabel } from '../src/kitchen-api.js';
const order = {id:1,status:'PENDIENTE_ACEPTACION',total:'24.00',items:[{variant_id:7,quantity:2,unit_price:'12.00',selected_flavors:['BBQ','Buffalo']}]};
test('products, variants, quantities and wing flavors have readable labels and historical fallback',()=>{
  assert.equal(itemLabel(order.items[0],[{name:'Alitas',variants:[{id:7,name:'12 unidades'}]}]),'2 x Alitas - 12 unidades | Sabores: BBQ, Buffalo');
  assert.match(itemLabel(order.items[0],[]),/Variante #7.*BBQ, Buffalo/);
});
test('queue and each transition use actual staff contracts and verify responses',async()=>{
  const original = globalThis.fetch; let current = {...order};
  globalThis.fetch = async(path,options)=>{
    assert.equal(options.headers.Authorization,'Bearer secret');
    if(options.method === 'PATCH') {assert.equal(path,'/kitchen/orders/1/status'); const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['status']);current={...current,status:body.status}; return Response.json(current);}
    assert.equal(path,'/kitchen/orders'); return Response.json([current]);
  };
  try {assert.equal((await kitchenQueue('secret')).length,1);for(const status of ['PREPARANDO','LISTO','ENVIADO']){current = await advanceOrder(current,'secret');assert.equal(current.status,status);}await assert.rejects(advanceOrder(current,'secret'));}
  finally {globalThis.fetch=original;}
});
test('errors and malformed successes never claim confirmed state',async()=>{
  const original=globalThis.fetch;
  try {
    for(const status of [401,403,409]) {globalThis.fetch=async()=>Response.json({}, {status});await assert.rejects(advanceOrder(order,'secret'),e=>e.status===status);}
    globalThis.fetch=async()=>{throw Error('offline');}; await assert.rejects(advanceOrder(order,'secret'),e=>e.uncertain);
    globalThis.fetch=async()=>Response.json({...order,status:'LISTO'});await assert.rejects(advanceOrder(order,'secret'),e=>e.uncertain);
    globalThis.fetch=async()=>Response.json([{...order,status:'ENVIADO'}]);await assert.rejects(kitchenQueue('secret'));
  } finally {globalThis.fetch=original;}
  assert.match(kitchenMessage({status:401}),/Sesion/); assert.match(kitchenMessage({status:403}),/KITCHEN/);assert.match(kitchenMessage({status:409}),/cambio/);assert.match(kitchenMessage({}),/Actualiza/);
});

test('stale proxy HTML 404 explains missing Kitchen route instead of an uncertain transition',async()=>{
  const original = globalThis.fetch;
  globalThis.fetch = async()=>new Response('<html>File not found</html>',{status:404,headers:{'Content-Type':'text/html'}});
  try {
    await assert.rejects(kitchenQueue('secret'),error=>{
      assert.equal(error.status,404);
      assert.match(kitchenMessage(error),/Reinicia el proxy frontend/);
      return true;
    });
  } finally {globalThis.fetch = original;}
  assert.match(kitchenMessage({status:502}),/FastAPI/);
});
