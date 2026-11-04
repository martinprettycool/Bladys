import { element as el } from './components.js';

// Presentation only: keys match GET /products categories; never duplicate catalog data.
const categoryImages = {
  PIZZA: 'pizzas',
  HAMBURGUESA: 'hamburguesas',
  ALITAS: 'alitas',
  PAPITAS: 'papitas',
};
const asset = name => new URL('../public/menu/' + name, import.meta.url).href;

export function categoryCard({ category, label, count, onNavigate, index = 0 }) {
  const photo = categoryImages[category];
  const media = el('span', { className: 'category-media' });
  const fallback = el('span', { className: 'category-photo-note', text: label, 'aria-hidden': 'true' });
  media.append(fallback);
  media.classList.toggle('is-placeholder', !photo);
  if (photo) {
    const image = el('img', {
      className: 'category-image', src: asset(photo + '-400.jpg'),
      srcset: `${asset(photo + '-400.jpg')} 400w, ${asset(photo + '-800.jpg')} 800w`,
      sizes: '(min-width: 1216px) 268px, (min-width: 768px) calc((100vw - 112px) / 4), calc((100vw - 43px) / 2)',
      alt: '', width: '400', height: '400', decoding: 'async',
      loading: index < 2 ? 'eager' : 'lazy',
    });
    image.addEventListener('error', () => {
      media.classList.add('is-placeholder');
      image.remove();
    }, { once: true });
    media.prepend(image);
  }
  const link = el('a', {
    className: 'category-card', href: '#category-' + category.toLowerCase(),
  }, [
    media,
    el('span', { className: 'category-caption' }, [
      el('strong', { text: label }),
      el('span', { className: 'category-detail', text: `${count} ${count === 1 ? 'opci\u00f3n' : 'opciones'}` }),
    ]),
  ]);
  link.addEventListener('click', onNavigate);
  return link;
}
