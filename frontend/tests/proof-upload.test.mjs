import test from 'node:test';
import assert from 'node:assert/strict';
import { createProofUpload, proofFileError, MAX_PROOF_SIZE } from '../src/proof-upload.js';
import { uploadOrderProof, ApiError } from '../src/api.js';
const order = (version = 1, total = '10.00') => ({ id: 7, version, total, status: 'PENDIENTE_ACEPTACION', items: [] });
const payment = (status = 'PENDIENTE_COMPROBANTE', proof_reference = null) => ({ id: 3, order_id: 7, status, proof_reference });
const file = (type = 'image/png', size = 10) => new File([new Uint8Array(size)], type === 'image/png' ? 'recibo.png' : 'recibo.jpg', { type });
function setup(options = {}) {
  return createProofUpload({ receipt: { order: order(), token: 'guest-capability' }, payment: payment(), read: async () => order(), readPayment: async () => payment(),
    makePreview: () => 'blob:preview', revokePreview: () => {}, ...options });
}
for (const type of ['image/jpeg', 'image/png']) test(`valid ${type} uploads once with reviewed version and real payment result`, async () => {
  let calls = 0, result;
  const upload = setup({ upload: async (id, token, chosen, version) => {
    calls++; assert.equal(id, 7); assert.equal(token, 'guest-capability'); assert.equal(chosen.type, type); assert.equal(version, 1);
    return payment('PENDIENTE_VERIFICACION', 'proof');
  }, onCurrent: (o, p) => result = { o, p } });
  upload.select(file(type)); upload.acknowledge(true);
  assert.equal(await upload.submit(), true); assert.equal(await upload.submit(), false);
  assert.equal(calls, 1); assert.equal(result.o.id, 7); assert.equal(result.o.total, '10.00'); assert.equal(result.o.version, 1);
  assert.equal(result.p.status, 'PENDIENTE_VERIFICACION'); assert.equal(upload.canSubmit, false);
  assert.match(upload.notice, /todav.*no est.*aprobado/);
});
for (const [type, size, message] of [['application/pdf', 10, /JPG o PNG/], ['image/png', MAX_PROOF_SIZE + 1, /5 MB/], ['image/png', 0, /vac/]]) test(`invalid selection ${type}/${size} cannot upload`, async () => {
  let calls = 0; const upload = setup({ upload: async () => calls++ });
  upload.select(file(type, size)); upload.acknowledge(true);
  assert.match(upload.error, message); assert.equal(await upload.submit(), false); assert.equal(calls, 0);
});
test('exactly 5 MB accepted; selecting another file and disposal revoke preview URLs', () => {
  assert.equal(proofFileError(file('image/jpeg', MAX_PROOF_SIZE)), '');
  let revoked = 0; const upload = setup({ revokePreview: () => revoked++ });
  upload.select(file()); upload.select(file()); upload.dispose(); assert.equal(revoked, 2);
});
test('review acknowledgement is required before upload', async () => {
  const upload = setup(); upload.select(file()); assert.equal(upload.canSubmit, false); assert.equal(await upload.submit(), false);
});
test('changed preflight version refreshes authoritative total without sending stale proof', async () => {
  let calls = 0; const upload = setup({ read: async () => order(2, '15.00'), upload: async () => calls++ });
  upload.select(file()); upload.acknowledge(true); assert.equal(await upload.submit(), false);
  assert.equal(calls, 0); assert.equal(upload.reviewed, false); assert.equal(upload.order.total, '15.00'); assert.match(upload.error, /total actualizado/);
});
test('server version conflict refreshes order and requires explicit review, never automatic retry', async () => {
  /* Contexto: el pedido cambia entre la consulta previa y la subida.
     Garantia: el conflicto no reenvia el archivo usando otra version.
     Escenarios: 409, consulta del total nuevo y confirmacion del usuario pendiente. */
  let reads = 0, calls = 0;
  const upload = setup({ read: async () => ++reads === 1 ? order() : order(2, '15.00'), upload: async () => { calls++; throw new ApiError('conflict', { status: 409, code: 'ORDER_VERSION_CONFLICT' }); } });
  upload.select(file()); upload.acknowledge(true); assert.equal(await upload.submit(), false);
  assert.equal(calls, 1); assert.equal(upload.order.version, 2); assert.equal(upload.reviewed, false); assert.equal(upload.canSubmit, false);
});
for (const status of [401, 403]) test(`capability failure ${status} prevents uploading and does not clear credentials`, async () => {
  let calls = 0; const upload = setup({ read: async () => { throw new ApiError('denied', { status }); }, upload: async () => calls++ });
  upload.select(file()); upload.acknowledge(true); assert.equal(await upload.submit(), false); assert.equal(calls, 0); assert.equal(upload.canSubmit, false); assert.match(upload.error, /verificar|expir/);
});
test('payment advances before upload: preflight prevents another file', async () => {
  let calls = 0; const upload = setup({ readPayment: async () => payment('APROBADO', 'existing'), upload: async () => calls++ });
  upload.select(file()); upload.acknowledge(true); assert.equal(await upload.submit(), false); assert.equal(calls, 0);
});
test('lost response with committed proof consults actual payment without claiming approval or resubmitting', async () => {
  /* Contexto: la subida confirma en el servidor pero se pierde la respuesta.
     Garantia: se consulta el pago y no se duplica la solicitud ni se afirma aprobacion.
     Escenarios: fallo incierto y comprobante visible en una consulta posterior. */
  let calls = 0, reads = 0;
  const upload = setup({ readPayment: async () => ++reads === 1 ? payment() : payment('PENDIENTE_VERIFICACION', 'proof'), upload: async () => { calls++; throw new ApiError('lost', { uncertain: true }); } });
  upload.select(file()); upload.acknowledge(true); assert.equal(await upload.submit(), false);
  assert.equal(upload.payment.status, 'PENDIENTE_VERIFICACION'); assert.equal(upload.canSubmit, false); assert.equal(calls, 1);
});
test('uncertain pending response blocks repeat until explicit consultation and total review', async () => {
  /* Contexto: se pierde una respuesta y el pago aun aparece pendiente.
     Garantia: no se repite la subida sin consulta y revision explicitas.
     Escenarios: respuesta incierta, consulta pendiente y nueva confirmacion del total. */
  let calls = 0; const upload = setup({ upload: async () => { calls++; throw new ApiError('offline', { uncertain: true }); } });
  upload.select(file()); upload.acknowledge(true); await upload.submit();
  upload.acknowledge(true); assert.equal(await upload.submit(), false); assert.equal(calls, 1); assert.equal(upload.uncertain, true);
  await upload.consult(); assert.equal(upload.reviewed, false); assert.equal(upload.canSubmit, false);
  upload.acknowledge(true); assert.equal(upload.canSubmit, true);
});
test('double click shares busy guard and never sends a second upload', async () => {
  /* Contexto: el cliente pulsa enviar dos veces mientras espera la respuesta.
     Garantia: solo una solicitud puede estar en curso desde este formulario.
     Escenarios: envio pendiente, segundo clic y confirmacion del primero. */
  let done, calls = 0;
  const upload = setup({ upload: () => { calls++; return new Promise(resolve => { done = resolve; }); } });
  upload.select(file()); upload.acknowledge(true); const pending = upload.submit();
  assert.equal(await upload.submit(), false); await new Promise(resolve => setImmediate(resolve));
  done(payment('PENDIENTE_VERIFICACION', 'proof')); assert.equal(await pending, true); assert.equal(calls, 1);
});
test('FormData API uses order ID, guest header and native multipart boundary with file/version only', async () => {
  const chosen = file();
  const result = await uploadOrderProof(7, 'guest-secret', chosen, 4, { fetchImpl: async (url, options) => {
    assert.equal(url, '/orders/7/payment/proof'); assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-Order-Access-Token'], 'guest-secret'); assert.equal(options.headers['Content-Type'], undefined);
    assert.deepEqual([...options.body.keys()], ['file', 'expected_version']); assert.equal(options.body.get('expected_version'), '4'); assert.equal(options.body.get('file').name, chosen.name);
    return Response.json(payment('PENDIENTE_VERIFICACION', 'proof'));
  } }); assert.equal(result.status, 'PENDIENTE_VERIFICACION');
});
for (const token of [undefined, 'invalid']) test(`upload API handles ${token ? 'invalid' : 'missing'} capability as unauthorized`, async () => {
  await assert.rejects(uploadOrderProof(7, token, file(), 1, { fetchImpl: async () => Response.json({ detail: 'Invalid Order access credentials' }, { status: 401 }) }), e => e.status === 401 && !e.uncertain);
});
test('malformed successful upload response is uncertain and requires reconciliation', async () => {
  await assert.rejects(uploadOrderProof(7, 'cap', file(), 1, { fetchImpl: async () => Response.json(payment('APROBADO', 'proof')) }), e => e.uncertain);
});

test('syncing a refreshed order preserves the file but invalidates the previous total acknowledgement', () => {
  const upload = setup(), chosen = file(); upload.select(chosen); upload.acknowledge(true);
  upload.setCurrent(order(2, '12.00'), payment());
  assert.equal(upload.file, chosen); assert.equal(upload.order.version, 2); assert.equal(upload.reviewed, false); assert.equal(upload.canSubmit, false);
});
