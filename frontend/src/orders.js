import { element as el, button, state, badge } from './components.js';
import { getOrder, getOrderPayment, getProducts } from './api.js';
import { summaryRows } from './checkout.js';
import { available, cents, money } from './cart.js';
import { configureProduct } from './configurator.js';
import { createOrderEditor, editingReason } from './order-editor.js';
import { createProofUpload } from './proof-upload.js';
import { proofPanel } from './proof-panel.js';

export function ordersScreen({ checkout, dialog, latest = false }) {
  const node = el('section', { className: 'customer-shell checkout-shell', 'aria-labelledby': 'orders-title' });
  const records = new Map();
  const uploads = new Map();
  const uploading = () => [...uploads.values()].some(upload => upload.busy);
  let active = true, selected = latest ? checkout.receipt?.order.id : null, editor = null, products = [], editing = false;
  let loading = false, message = '';
  const entry = () => checkout.receipts.find(r => r.order.id === selected);
  function accessMessage(error) {
    return [401, 403].includes(error.status) ? 'El acceso no es válido o expiró. Contacta al restaurante; no crearemos otro pedido.'
      : error.status === 404 ? 'El pedido o su pago no están disponibles. Contacta al restaurante.' : 'No pudimos actualizar. Se muestra la última información guardada.';
  }
  async function refreshOne(receipt) {
    const record = { verified: false, payment: null, message: '' };
    records.set(receipt.order.id, record);
    try {
      const order = await getOrder(receipt.order.id, receipt.token);
      if (!active) return;
      checkout.updateOrder(order);
      record.payment = await getOrderPayment(order.id, receipt.token);
      record.verified = true;
      uploads.get(order.id)?.setCurrent(order, record.payment);
    } catch (error) { record.message = accessMessage(error); }
  }
  async function refresh() {
    if (loading || editor?.busy || uploading()) return;
    loading = true; render();
    if (editor) {
      try {
        await editor.refresh();
        records.set(selected, { verified: true, payment: editor.payment, message: '' });
        message = 'Estado actualizado. El borrador conserva su versión original. Para usar la versión actual, descarta el borrador e inicia otra edición.';
      } catch (error) { records.set(selected, { verified: false, payment: null, message: accessMessage(error) }); message = accessMessage(error); }
    } else {
      try { products = await getProducts(); } catch { /* Saved labels remain usable offline. */ }
      for (const receipt of checkout.receipts) await refreshOne(receipt);
    }
    loading = false; if (active) render();
  }
  async function beginEdit() {
    const receipt = entry(); if (!receipt || loading || uploading() || uploads.get(selected)?.uncertain) return;
    loading = true; message = ''; render();
    try {
      await refreshOne(receipt);
      const fresh = entry(), record = records.get(selected);
      if (!record?.verified || editingReason(fresh.order, record.payment)) throw new Error(record?.message || editingReason(fresh.order, record?.payment));
      products = await getProducts();
      if (!active) return;
      editor = createOrderEditor({ receipt: fresh, products, onUpdate: (order, labels) => checkout.updateOrder(order, labels) });
      editing = true;
    } catch (error) { message = error.message; }
    finally { loading = false; if (active) render(); }
  }
  function orderContent(receipt, detail) {
    const order = receipt.order, record = records.get(order.id);
    const reason = uploads.get(order.id)?.uncertain ? 'Consulta el estado del comprobante antes de modificar el pedido.' : record?.verified ? editingReason(order, record.payment) : 'Actualiza el pedido para verificar si permite modificaciones.';
    const box = el('article', { className: 'card guest-order', 'data-order-id': order.id }, [
      el('h2', { className: 'order-number', text: `Pedido #${order.id}` }), badge(order.status.replaceAll('_', ' ')),
      summaryRows(order.items, [...(receipt.items || []), ...products.flatMap(product => product.variants.map(variant => ({ product, variant, selected_flavors: [] })))], true),
      el('div', { className: 'cart-total' }, [el('strong', { text: 'Total confirmado' }), el('strong', { className: 'confirmed-total', text: money(cents(order.total)) })]),
      el('p', { className: 'muted', text: record?.payment ? `Pago: ${record.payment.status.replaceAll('_', ' ')}` : 'Pago: información no disponible' }),
    ]);
    if (record?.message) box.append(el('p', { role: 'alert', text: record.message }));
    if (detail) {
      box.append(button('Modificar pedido', { disabled: loading || uploading() || !!reason, onClick: beginEdit }));
      if (reason) box.append(el('p', { className: 'muted', text: reason }));
      if (record?.verified) {
        if (!uploads.has(order.id)) uploads.set(order.id, createProofUpload({ receipt, payment: record.payment,
          onCurrent: (current, payment) => { checkout.updateOrder(current); records.set(order.id, { verified: true, payment, message: '' }); }, onChange: render }));
        uploads.get(order.id).setCurrent(order, record.payment);
        box.append(proofPanel(uploads.get(order.id)));
      }
    } else box.append(button('Ver pedido', { variant: 'secondary', onClick: () => { selected = order.id; message = ''; render(); window.scrollTo(0, 0); } }));
    return box;
  }
  function renderEditor() {
    node.append(el('h2', { text: `Modificar pedido #${selected}` }), el('p', { className: 'muted', text: 'Este borrador es independiente de tu carrito. El restaurante calcula el total al guardar.' }));
    for (const item of editor.draft.items) {
      const change = delta => { editor.draft.change(item.key, delta); render(); };
      const locked = editor.busy || loading;
      node.append(el('article', { className: 'cart-item' }, [
        el('div', { className: 'cart-item-copy' }, [el('h3', { text: item.product.name }), el('p', { text: item.variant.name }),
          el('p', { text: item.selected_flavors.join(' + ') }), ...(!available(item.product, item.variant) ? [el('p', { text: 'No disponible: quita o cambia esta selección.' })] : [])]),
        el('div', { className: 'quantity-control' }, [button('−', { variant: 'secondary', disabled: locked, ariaLabel: 'Reducir ' + item.product.name, onClick: () => change(-1) }),
          el('output', { text: item.quantity }), button('+', { variant: 'secondary', disabled: locked, ariaLabel: 'Aumentar ' + item.product.name, onClick: () => change(1) })]),
        button('Cambiar opción', { variant: 'secondary', disabled: locked || !item.product.variants, onClick: () => configureProduct(dialog, item.product, (p, v, q, f) => {
          editor.draft.change(item.key, -item.quantity); editor.draft.add(p, v, q, f); render();
        }) }),
        button('Quitar', { variant: 'quiet', disabled: locked, onClick: () => change(-item.quantity) }),
      ]));
    }
    node.append(el('div', { className: 'cart-total' }, [el('strong', { text: 'Total estimado del borrador' }), el('strong', { text: money(editor.draft.total) })]));
    const add = el('section', { className: 'section-stack' }, [el('h2', { text: 'Agregar productos al pedido' })]);
    for (const product of products) add.append(button(product.name, { variant: 'secondary', disabled: editor.busy || loading || !product.variants.some(v => available(product, v)),
      onClick: () => configureProduct(dialog, product, (p, v, q, f) => { editor.draft.add(p, v, q, f); render(); }) }));
    node.append(add);
    if (editor.message) node.append(el('p', { className: 'checkout-error', role: 'alert', text: editor.message }));
    if (message) node.append(el('p', { className: 'checkout-notice', role: 'status', text: message }));
    if (editor.blocked) node.append(el('section', { className: 'notice' }, [el('h2', { text: 'Último estado consultado del pedido' }), orderContent(entry(), false)]));
    const save = button(editor.busy ? 'Guardando…' : 'Guardar modificaciones', { disabled: editor.busy || loading || editor.blocked || !editor.draft.count || editor.draft.items.some(i => !available(i.product, i.variant)), onClick: async () => {
      const operation = editor.save(); render();
      const success = await operation;
      if (success) { editor = null; editing = false; message = 'Modificaciones guardadas. El total mostrado fue calculado por el restaurante.'; await refresh(); }
      if (active) render();
    } });
    node.append(el('div', { className: 'checkout-actions' }, [save,
      button('Actualizar pedido actual', { variant: 'secondary', disabled: loading || editor.busy, onClick: refresh }),
      button('Descartar borrador y volver', { variant: 'quiet', disabled: editor.busy || loading, onClick: () => { editor = null; editing = false; message = ''; render(); } }),
    ]));
  }
  function render() {
    if (!active) return;
    node.replaceChildren(el('h1', { id: 'orders-title', text: 'Mis pedidos' }));
    if (editing && editor) { renderEditor(); return; }
    if (message) node.append(el('p', { className: 'checkout-notice', role: 'status', text: message }));
    if (checkout.storageWarning) node.append(el('p', { className: 'checkout-error', role: 'alert', text: 'No pudimos guardar los accesos en esta pestaña. Mantenla abierta y guarda el acceso antes de salir.' }));
    if (checkout.confirmedRecovery) node.append(button('Guardar acceso al pedido', { variant: 'secondary', onClick: async () => { await checkout.submit(); render(); } }));
    const receipts = checkout.receipts;
    if (!receipts.length) node.append(state({ title: 'No hay pedidos guardados en esta pestaña', description: 'Aquí aparecerán los pedidos que confirmes desde esta sesión.' }));
    else if (selected && entry()) node.append(orderContent(entry(), true), button('Ver todos mis pedidos', { variant: 'secondary', onClick: () => { selected = null; render(); } }));
    else for (const receipt of [...receipts].reverse()) node.append(orderContent(receipt, false));
    node.append(el('p', { className: 'muted', text: 'Accesos guardados en esta pestaña, no un historial de cuenta. Cerrar la pestaña o la navegación privada puede perderlos; los tokens pueden expirar. Si pierdes acceso, contacta al restaurante.' }),
      el('div', { className: 'checkout-actions' }, [button(loading ? 'Consultando…' : 'Actualizar estado', { variant: 'secondary', disabled: loading || uploading(), onClick: refresh }), el('a', { className: 'button button-quiet', href: '#menu', text: 'Volver al menú' })]));
  }
  render(); refresh();
  return { node, cleanup: () => { active = false; for (const upload of uploads.values()) upload.dispose(); } };
}
