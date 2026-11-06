import { validOrder, request } from './api.js';
import { element as el, button, input, state, card } from './components.js';
import { login, staffRequest, proofBlob, staffMessage, decidePayment } from './reception-api.js';

export function receptionScreen() {
  const node = el('section', {className:'staff-workspace reception-workspace'});
  let token = '', disposed = false, generation = 0, busy = false;
  const urls = new Set();
  let controller = new AbortController();
  function release() { controller.abort(); controller = new AbortController(); for (const url of urls) URL.revokeObjectURL(url); urls.clear(); }
  function signIn(message = '') {
    token = ''; generation++; busy = false; release();
    const username = input('Usuario', {id:'staff-user'}), password = input('Contrasena', {id:'staff-password',type:'password'});
    username.control.autocomplete = 'username'; password.control.autocomplete = 'current-password';
    const notice = el('p', {role:'alert',text:message});
    const submit = el('button', {type:'submit',className:'button button-primary',text:'Iniciar sesion'});
    const form = el('form', {}, [username.wrapper,password.wrapper,submit,notice]);
    let pending = false;
    const loginGeneration = generation;
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (pending) return; pending = true; submit.disabled = true;
      try {
        const result = await login(username.control.value, password.control.value);
        password.control.value = '';
        if (disposed || loginGeneration !== generation) return;
        if (typeof result.access_token !== 'string' || result.token_type !== 'bearer') throw new Error();
        token = result.access_token; await refresh();
      } catch (error) { if (!disposed) notice.textContent = staffMessage(error); }
      finally {pending = false; submit.disabled = false;}
    });
    node.replaceChildren(el('h1',{text:'Recepcion: verificar pagos'}),form);
  }
  async function refresh(message = '') {
    if (disposed) return;
    const current = ++generation; release(); busy = true;
    node.replaceChildren(state({kind:'loading',title:'Consultando pagos',description:'Esperando datos del restaurante.'}));
    try {
      const [payments, products] = await Promise.all([
        staffRequest('/payments/pending', token), request('/products').catch(() => []),
      ]);
      const labels = new Map();
      if (Array.isArray(products)) for (const product of products) {
        if (Array.isArray(product.variants)) for (const variant of product.variants)
          labels.set(variant.id, `${product.name} - ${variant.name}`);
      }
      if (!Array.isArray(payments) || payments.some(p => !Number.isSafeInteger(p.id) || p.id < 1 || !Number.isSafeInteger(p.order_id) || p.order_id < 1 || p.status !== 'PENDIENTE_VERIFICACION' || typeof p.proof_reference !== 'string')) throw new Error();
      const orders = await Promise.all(payments.map(p => staffRequest(`/reception/orders/${p.order_id}`,token)));
      if (disposed || current !== generation) return;
      if (orders.some((order,index) => !validOrder(order) || order.id !== payments[index].order_id)) throw new Error();
      const logout = button('Cerrar sesion',{onClick:()=>signIn()});
      const update = button('Actualizar estado',{onClick:()=>refresh()});
      node.replaceChildren(el('h1',{text:'Pagos pendientes de verificacion'}),logout,update,el('p',{role:'status',text:message}),el('p',{text:'Esta cola incluye comprobantes enviados. Los pedidos sin comprobante no se consultan en esta API.'}));
      if (!payments.length) node.append(state({title:'No hay pagos por verificar',description:'Actualiza para consultar nuevos comprobantes.'}));
      payments.forEach((payment,index) => {
        const order = orders[index];
        const proof = el('div');
        const notice = el('p',{role:'status'});
        let viewed = false, proofUrl = null;
        const inspect = button('Ver comprobante',{onClick:async()=>{
          if (busy) return;
          inspect.disabled = true; viewed = false; notice.textContent = 'Cargando comprobante...';
          try {
            const blob = await proofBlob(payment.id,token,{signal:controller.signal});
            if (disposed || current !== generation) return;
            if (proofUrl) {URL.revokeObjectURL(proofUrl); urls.delete(proofUrl);}
            const url = URL.createObjectURL(blob); proofUrl = url; urls.add(url);
            const image = el('img',{src:url,alt:`Comprobante del pedido ${order.id}`,style:'max-width:100%;max-height:70vh;object-fit:contain'});
            image.addEventListener('load',()=>{viewed = true; notice.textContent = 'Comprobante cargado. Revisa la imagen y el total antes de decidir.';});
            image.addEventListener('error',()=>{viewed = false; notice.textContent = 'No se pudo mostrar la imagen.';});
            proof.replaceChildren(image);
          } catch(error) {if (!disposed && current === generation) { notice.textContent = staffMessage(error); if(error.status === 401) signIn(staffMessage(error)); }}
          finally {inspect.disabled = busy;}
        }});
        const actions = [];
        async function decide(status) {
          if (busy) return;
          if (!viewed) {notice.textContent = 'Abre y revisa el comprobante antes de decidir.'; return;}
          const label = status === 'APROBADO' ? 'aprobar el pago' : 'rechazar el pago y cancelar el pedido';
          if (!window.confirm(`Confirmas ${label} del pedido ${order.id}? Esta decision no se puede repetir ni revertir desde esta pantalla.`)) return;
          busy = true; for (const b of node.querySelectorAll('button')) b.disabled = true;
          let message;
          try {
            await decidePayment(payment,status,token);
            message = `Servidor confirmo ${status} para el pedido ${order.id}.`;
          } catch(error) {if (disposed || current !== generation) return; message = staffMessage(error); if (error.status === 401) {signIn(message); busy = false; return;}}
          if (!disposed && current === generation) await refresh(message);
        }
        actions.push(button('Aprobar pago',{onClick:()=>decide('APROBADO')}),button('Rechazar pago y cancelar pedido',{variant:'secondary',onClick:()=>decide('RECHAZADO')}));
        node.append(card([el('h2',{text:`Pedido #${order.id}`}),el('p',{text:`Pedido: ${order.status} | Pago: ${payment.status}`}),
          el('ul',{},order.items.map(item=>el('li',{text:`${item.quantity} x ${labels.get(item.variant_id) || 'Variante #'+item.variant_id}${item.selected_flavors.length ? ' | Sabores: '+item.selected_flavors.join(', ') : ''}`}))),
          el('p',{text:`Total confirmado: $${order.total}`}),inspect,proof,...actions,notice]));
      });
    } catch(error) {
      if (disposed || current !== generation) return;
      if (error.status === 401) {signIn(staffMessage(error)); return;}
      node.replaceChildren(state({kind:'error',title:'No se pudo consultar la cola',description:message+' '+staffMessage(error),action:button('Reintentar',{onClick:()=>refresh()})}),button('Cerrar sesion',{onClick:()=>signIn()}));
    } finally {if (current === generation) busy = false;}
  }
  signIn();
  return {node,cleanup(){disposed = true; generation++; token = ''; release();}};
}
