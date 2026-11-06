import { element as el, button } from './components.js';
import { cents, money } from './cart.js';
import { editingReason } from './order-editor.js';
export function proofPanel(upload) {
  const order = upload.order, payment = upload.payment;
  const panel = el('section', { className: 'proof-panel notice', 'aria-labelledby': 'proof-title', 'aria-busy': String(upload.busy) }, [
    el('h2', { id: 'proof-title', text: 'Comprobante de transferencia' }),
    el('p', { text: `Pedido #${order.id} · Total actual: ${money(cents(order.total))}` }),
    el('p', { className: 'proof-payment-status', text: `Pago: ${payment.status.replaceAll('_', ' ')}` }),
    el('p', { className: 'muted', text: 'Datos bancarios pendientes de configuración. Contacta al restaurante para obtener instrucciones verificadas antes de transferir.' }),
  ]);
  if (payment.status === 'PENDIENTE_VERIFICACION') panel.append(el('p', { className: 'checkout-notice', text: 'Recepción debe verificar el comprobante. Enviarlo no significa que el pago esté aprobado.' }));
  const reason = editingReason(order, payment);
  if (reason) panel.append(el('p', { text: reason }));
  else {
    const picker = el('input', { type: 'file', id: 'proof-file', accept: 'image/jpeg,image/png,.jpg,.jpeg,.png' });
    picker.disabled = upload.busy;
    if (upload.file) {
      try { const chosen = new DataTransfer(); chosen.items.add(upload.file); picker.files = chosen.files; }
      catch { /* Filename and preview still identify the selection. */ }
    }
    picker.addEventListener('change', () => { if (picker.files[0]) upload.select(picker.files[0]); });
    panel.append(el('label', { className: 'field', for: 'proof-file' }, [el('span', { text: 'Selecciona JPG o PNG, hasta 5 MB' }), picker]));
    if (upload.file) panel.append(el('p', { className: 'proof-filename', text: `Archivo: ${upload.file.name}` }));
    if (upload.preview) {
      const image = el('img', { className: 'proof-preview', src: upload.preview, alt: 'Vista previa del comprobante seleccionado' });
      image.addEventListener('error', () => { image.hidden = true; });
      panel.append(image);
    }
    const confirm = el('input', { type: 'checkbox', id: 'proof-reviewed' });
    confirm.checked = upload.reviewed; confirm.disabled = upload.busy || upload.uncertain;
    confirm.addEventListener('change', () => upload.acknowledge(confirm.checked));
    panel.append(el('label', { className: 'proof-review', for: 'proof-reviewed' }, [confirm, el('span', { text: `He revisado el pedido #${order.id} y su total actual de ${money(cents(order.total))}.` })]),
      button(upload.busy ? 'Enviando o consultando…' : 'Enviar comprobante', { disabled: !upload.canSubmit, className: 'proof-submit', onClick: () => upload.submit() }));
  }
  if (upload.notice) panel.append(el('p', { className: 'checkout-notice proof-notice', role: 'status', text: upload.notice }));
  if (upload.error) panel.append(el('p', { className: 'checkout-error proof-error', role: 'alert', text: upload.error }));
  panel.append(button('Consultar estado del pago', { variant: 'secondary', disabled: upload.busy, className: 'proof-consult', onClick: () => upload.consult() }));
  return panel;
}
