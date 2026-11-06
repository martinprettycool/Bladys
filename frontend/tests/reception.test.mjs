import test from 'node:test';
import assert from 'node:assert/strict';
import { proofBlob, staffRequest, login, staffMessage, decidePayment } from '../src/reception-api.js';

test('protected binary proof uses authorization header and no URL credential', async () => {
  const blob = new Blob(['proof'], {type:'image/png'});
  const result = await proofBlob(7,'secret',{fetchImpl:async(path,options)=>{
    assert.equal(path,'/payments/7/proof'); assert.equal(options.headers.Authorization,'Bearer secret');
    assert.equal(options.cache,'no-store'); return new Response(blob);
  }});
  assert.equal(await result.text(),'proof');
});
test('proof handles permission, network and invalid image responses', async () => {
  for (const status of [401,403,404,409]) await assert.rejects(proofBlob(7,'secret',{fetchImpl:async()=>new Response('',{status})}),error=>error.status === status);
  await assert.rejects(proofBlob(7,'secret',{fetchImpl:async()=>{throw new Error('offline');}}));
  await assert.rejects(proofBlob(7,'secret',{fetchImpl:async()=>new Response('{}',{headers:{'Content-Type':'application/json'}})}));
});
test('actual login and decision contracts send no totals or URL tokens', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async(path,options)=>{calls.push([path,options]);return new Response(JSON.stringify({status:'APROBADO'}));};
  try {
    await login('reception','password');
    await staffRequest('/payments/7/status','secret',{method:'PATCH',body:JSON.stringify({status:'APROBADO'})});
    assert.equal(calls[0][0],'/login'); assert.deepEqual(JSON.parse(calls[0][1].body),{username:'reception',password:'password'});
    assert.equal(calls[1][0],'/payments/7/status'); assert.equal(calls[1][1].headers.Authorization,'Bearer secret');
    assert.deepEqual(JSON.parse(calls[1][1].body),{status:'APROBADO'});
  } finally {globalThis.fetch = original;}
});
test('staff errors distinguish expired session, permissions, conflict and uncertain outcome',()=>{
  assert.match(staffMessage({status:401}),/Sesion/); assert.match(staffMessage({status:403}),/permiso/);
  assert.match(staffMessage({status:409}),/Otro proceso/); assert.match(staffMessage({status:0}),/no repitas/);
});

test('malformed decision success remains uncertain and cannot claim approval',async()=>{
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async()=>new Response(JSON.stringify({id:8,order_id:2,status:'APROBADO'}));
    await assert.rejects(decidePayment({id:7,order_id:2},'APROBADO','secret'), error=>error.uncertain === true);
  } finally {globalThis.fetch = original;}
});
