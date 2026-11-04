// Tiny DOM helpers keep dynamic content out of HTML strings.
export function element(tag, { className, text, ...attributes } = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  node.append(...children);
  return node;
}

export function button(label, { variant = 'primary', onClick, disabled = false, ariaLabel, className = '' } = {}) {
  const node = element('button', { className: 'button button-' + variant + ' ' + className, type: 'button', text: label });
  node.disabled = disabled;
  if (ariaLabel) node.setAttribute('aria-label', ariaLabel);
  if (onClick) node.addEventListener('click', onClick);
  return node;
}

export function badge(label, tone = 'neutral') {
  return element('span', { className: 'badge badge-' + tone, text: label });
}

export function input(label, { id, type = 'text', placeholder = '' } = {}) {
  const control = element('input', { id, type, placeholder, className: 'input' });
  return { wrapper: element('label', { className: 'field', for: id }, [element('span', { text: label }), control]), control };
}

export function card(children) {
  return element('section', { className: 'card' }, children);
}

export function state({ kind = 'empty', title, description, action }) {
  const children = [];
  if (kind === 'loading') children.push(element('span', { className: 'spinner', 'aria-hidden': 'true' }));
  children.push(element('h3', { text: title }), element('p', { text: description }));
  if (action) children.push(action);
  return element('section', { className: 'state state-' + kind, role: kind === 'error' ? 'alert' : 'status' }, children);
}

// Crop only the mascot inside the source's Italian circle. The original JPEG is unchanged.
// The exterior black rectangle and small raster lettering are outside this viewport.
let brandInstance = 0;
export function brand({ href = '#menu' } = {}) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const clipId = 'brand-mascot-' + ++brandInstance;
  svg.setAttribute('viewBox', '120 20 560 560');
  svg.setAttribute('class', 'brand-mascot');
  svg.setAttribute('aria-hidden', 'true');
  const defs = document.createElementNS(ns, 'defs');
  const clip = document.createElementNS(ns, 'clipPath');
  clip.id = clipId;
  const circle = document.createElementNS(ns, 'circle');
  for (const [key, value] of Object.entries({ cx: 400, cy: 300, r: 280 })) circle.setAttribute(key, value);
  clip.append(circle);
  defs.append(clip);
  const image = document.createElementNS(ns, 'image');
  for (const [key, value] of Object.entries({
    href: new URL('../public/brand/bladys-logo.jpeg', import.meta.url).href,
    width: 800, height: 808, 'clip-path': 'url(#' + clipId + ')',
  })) image.setAttribute(key, value);
  svg.append(defs, image);
  return element('a', { href, className: 'brand', 'aria-label': "Blady's Pizzer\u00eda, inicio" }, [
    svg,
    element('span', { className: 'brand-lockup', 'aria-hidden': 'true' }, [
      element('span', { className: 'brand-name', text: "BLADY'S" }),
      element('span', { className: 'brand-descriptor', text: 'PIZZER\u00cdA' }),
    ]),
  ]);
}
