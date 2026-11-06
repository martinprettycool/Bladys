import { getOrder, getOrderPayment, uploadOrderProof } from './api.js';
import { editingReason } from './order-editor.js';
export const MAX_PROOF_SIZE = 5 * 1024 * 1024;
export function proofFileError(file) {
  if (!file || !['image/jpeg', 'image/png'].includes(file.type)) return 'Selecciona una imagen JPG o PNG.';
  if (file.size === 0) return 'El archivo está vacío.';
  if (file.size > MAX_PROOF_SIZE) return 'El comprobante no puede superar 5 MB (5.242.880 bytes).';
  return '';
}

// The acknowledgement binds the visible total to one version; refreshing never sends a file.
export function createProofUpload({ receipt, payment: initialPayment, read = getOrder, readPayment = getOrderPayment,
  upload = uploadOrderProof, onCurrent = () => {}, onChange = () => {},
  makePreview = file => URL.createObjectURL(file), revokePreview = url => URL.revokeObjectURL(url) }) {
  let order = receipt.order, payment = initialPayment, file = null, preview = '', reviewed = false;
  let busy = false, uncertain = false, error = '', notice = '', disposed = false;
  const changed = () => { if (!disposed) onChange(); };
  function current(nextOrder, nextPayment) {
    if (order.version !== nextOrder.version || order.total !== nextOrder.total) reviewed = false;
    order = nextOrder; payment = nextPayment;
  }
  function apply(nextOrder, nextPayment) { current(nextOrder, nextPayment); onCurrent(order, payment); }
  async function fetchCurrent() {
    const nextOrder = await read(receipt.order.id, receipt.token);
    const nextPayment = await readPayment(receipt.order.id, receipt.token);
    apply(nextOrder, nextPayment);
  }
  const reason = () => editingReason(order, payment);
  return {
    get order() { return order; }, get payment() { return payment; }, get file() { return file; }, get preview() { return preview; },
    get reviewed() { return reviewed; }, get busy() { return busy; }, get uncertain() { return uncertain; },
    get error() { return error; }, get notice() { return notice; },
    get canSubmit() { return !!file && reviewed && !busy && !uncertain && !reason(); },
    setCurrent: current,
    select(nextFile) {
      if (busy) return;
      if (preview) revokePreview(preview);
      preview = ''; file = null; error = proofFileError(nextFile); notice = '';
      if (!error) { file = nextFile; try { preview = makePreview(file); } catch { /* Upload remains possible without a preview. */ } }
      changed();
    },
    acknowledge(value) { if (!busy) { reviewed = !!value; changed(); } },
    async consult() {
      if (busy) return;
      busy = true; reviewed = false; error = ''; changed();
      try {
        await fetchCurrent(); uncertain = false;
        notice = payment.proof_reference !== null || payment.status !== 'PENDIENTE_COMPROBANTE'
          ? 'Estado consultado: el pago ya tiene un comprobante o ha avanzado. No se enviará otro archivo.'
          : 'No hay comprobante registrado al consultar. Revisa el total actual y confírmalo antes de enviar; no se ha reenviado ningún archivo.';
      } catch (failure) { uncertain = true; error = [401, 403].includes(failure.status) ? 'El acceso no es válido o expiró. Contacta al restaurante.' : 'No pudimos verificar el estado. Consulta antes de volver a enviar.'; }
      finally { busy = false; changed(); }
    },
    async submit() {
      if (!this.canSubmit) return false;
      const version = order.version, total = order.total;
      busy = true; error = ''; notice = ''; changed();
      let sent = false;
      try {
        // Catch an already changed total before sending bytes. The server handles races after this read.
        await fetchCurrent();
        if (version !== order.version || total !== order.total) {
          reviewed = false; error = 'El pedido cambió. Revisa el total actualizado y confírmalo antes de volver a enviar.';
          return false;
        }
        if (reason()) { reviewed = false; error = reason(); return false; }
        sent = true;
        const result = await upload(receipt.order.id, receipt.token, file, version);
        apply(order, result); reviewed = false;
        notice = 'Comprobante recibido. El pago está pendiente de verificación por recepción; todavía no está aprobado.';
        return true;
      } catch (failure) {
        reviewed = false;
        if (sent && failure.uncertain) {
          uncertain = true;
          notice = 'No pudimos confirmar el envío. Consultaremos el estado; no volveremos a subir el archivo automáticamente.';
          try {
            await fetchCurrent();
            if (payment.proof_reference !== null || payment.status !== 'PENDIENTE_COMPROBANTE') {
              uncertain = false;
              notice = 'El servidor registra un comprobante o un pago avanzado. Revisa el estado mostrado; no se enviará otro archivo.';
            } else notice += ' Aún no consta un comprobante. Pulsa Consultar estado del pago antes de decidir si reintentas.';
          } catch { error = 'No pudimos consultar el pago. Consulta su estado antes de volver a enviar.'; }
        } else if (failure.status === 409) {
          uncertain = true;
          try { await fetchCurrent(); uncertain = false; } catch { /* Keep upload blocked until a successful explicit consultation. */ }
          error = failure.code === 'ORDER_VERSION_CONFLICT'
            ? 'El pedido cambió mientras se enviaba el archivo. Revisa el total actual y confírmalo antes de intentar de nuevo.'
            : 'El pedido o pago ya no permiten enviar otro comprobante. Consulta el estado actual.';
        } else {
          error = [401, 403].includes(failure.status) ? 'El acceso no es válido o expiró. Contacta al restaurante.'
            : failure.status === 413 ? 'El comprobante supera el tamaño permitido de 5 MB.'
            : failure.status === 415 ? 'El comprobante debe ser JPG o PNG.'
            : sent ? 'No se pudo registrar el comprobante. Revisa el archivo y consulta el estado antes de reintentar.' : 'No pudimos verificar el pedido y su pago. Actualiza antes de enviar.';
          // Failed preflight/authentication must not leave stale authorization usable.
          if (!sent || [401, 403, 404].includes(failure.status)) uncertain = true;
        }
        return false;
      } finally { busy = false; changed(); }
    },
    dispose() { disposed = true; if (preview) revokePreview(preview); },
  };
}
