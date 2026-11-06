import { request } from './api.js';
import { login } from './reception-api.js';
import { kitchenQueue, advanceOrder, kitchenMessage, itemLabel, actionLabel } from './kitchen-api.js';
import { element as el, button, input, card } from './components.js';

export function kitchenScreen() {
  const node = el('section', {className:'staff-workspace kitchen-workspace'});
  let token = '', disposed = false, generation = 0, busy = false, fresh = false;
  let orders = [], products = [], filter = 'TODOS';
  let notice, list, update, selector;
  function signIn(message = '') {
    token = ''; generation++; busy = false; fresh = false; orders = []; products = [];
    const username = input('Usuario', {id:'kitchen-user'}), password = input('Contrasena', {id:'kitchen-password',type:'password'});
    username.control.autocomplete = 'username'; password.control.autocomplete = 'current-password';
    const alert = el('p',{role:'alert',text:message});
    const submit = el('button',{type:'submit',className:'button button-primary',text:'Iniciar sesion'});
    const form = el('form',{},[username.wrapper,password.wrapper,submit,alert]);
    const current = generation; let pending = false;
    form.addEventListener('submit',async event=>{
      event.preventDefault(); if (pending) return; pending = true; submit.disabled = true;
      try {
        const result = await login(username.control.value,password.control.value);
        password.control.value = '';
        if (disposed || current !== generation) return;
        if (typeof result.access_token !== 'string' || result.token_type !== 'bearer') throw new Error();
        token = result.access_token; workspace(); await refresh();
      } catch(error) {if (!disposed && current === generation) alert.textContent = kitchenMessage(error);}
      finally {pending = false; submit.disabled = false;}
    });
    node.replaceChildren(el('h1',{text:'Cocina'}),form);
  }
  function workspace() {
    notice = el('p',{role:'status','aria-live':'polite'}); list = el('div',{className:'kitchen-orders'});
    update = button('Actualizar cola',{onClick:()=>refresh()});
    selector = el('select',{id:'kitchen-filter',className:'input'},['TODOS','PENDIENTE_ACEPTACION','PREPARANDO','LISTO'].map(s=>el('option',{value:s,text:s === 'TODOS' ? 'Todos los activos' : s})));
    selector.value = filter; selector.addEventListener('change',()=>{filter = selector.value; render();});
    node.replaceChildren(el('h1',{text:'Pedidos en cocina'}),
      el('div',{className:'component-row'},[update,button('Cerrar sesion',{variant:'secondary',onClick:()=>signIn()})]),
      el('label',{for:'kitchen-filter',text:'Mostrar pedidos'}),selector,notice,list);
  }
  function render() {
    update.disabled = busy; selector.disabled = busy;
    const visible = orders.filter(o=>filter === 'TODOS' || o.status === filter);
    list.replaceChildren(...visible.map(order=>{
      const action = button(actionLabel[order.status],{disabled:busy || !fresh,onClick:()=>advance(order)});
      const entry = card([el('h2',{text:`Pedido #${order.id}`}),el('p',{className:'kitchen-status',text:order.status}),
        el('ul',{},order.items.map(item=>el('li',{text:itemLabel(item,products)}))),action]);
      entry.dataset.orderId = order.id; return entry;
    }));
    if (!visible.length) list.append(el('p',{text:fresh ? 'No hay pedidos en esta vista.' : 'Actualiza para consultar los pedidos.'}));
  }
  async function refresh(message = '') {
    if (busy || disposed) return;
    busy = true; fresh = false; const current = generation;
    notice.textContent = message + ' Consultando estado del servidor...'; render();
    try {
      const [queue,catalog] = await Promise.all([kitchenQueue(token),request('/products').catch(()=>null)]);
      if (disposed || current !== generation) return;
      orders = queue; if (Array.isArray(catalog)) products = catalog;
      fresh = true; notice.textContent = message + ' Cola actualizada.' + (catalog === null ? ' Catalogo no disponible; se conservan etiquetas conocidas o IDs de variante.' : '');
    } catch(error) {
      if (disposed || current !== generation) return;
      if (error.status === 401 || error.status === 403) {signIn(kitchenMessage(error)); return;}
      notice.textContent = message + ' ' + kitchenMessage(error) + ' Los datos anteriores no estan actualizados; acciones bloqueadas.';
    } finally {if (!disposed && current === generation) {busy = false; render();}}
  }
  async function advance(order) {
    if (busy || !fresh || disposed) return;
    busy = true; fresh = false; const current = generation;
    render(); notice.textContent = `Confirmando pedido #${order.id}...`;
    let message;
    try {const result = await advanceOrder(order,token); message = `Servidor confirmo pedido #${result.id}: ${result.status}.`;}
    catch(error) {
      if (disposed || current !== generation) return;
      if (error.status === 401 || error.status === 403) {signIn(kitchenMessage(error)); return;}
      message = kitchenMessage(error);
    }
    if (disposed || current !== generation) return;
    busy = false; await refresh(message);
  }
  signIn();
  return {node,cleanup(){disposed = true; generation++; token = ''; orders = []; products = [];}};
}
