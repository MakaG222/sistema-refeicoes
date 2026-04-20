/*
 * Service Worker — Escola Naval Refeições
 *
 * Estratégia intencionalmente conservadora:
 *   • Assets estáticos (CSS/JS/imagens em /static/) → stale-while-revalidate
 *   • Navegações (GET de páginas) → network-first com fallback à última cópia
 *     em cache para leitura offline da página atual
 *   • POST / APIs / admin / autenticação → pass-through (nunca cacheado)
 *
 * O objetivo não é "app offline completa" — é:
 *   1. arranque mais rápido em mobile,
 *   2. resiliência se a rede falhar por segundos,
 *   3. poder instalar como PWA no ambiente escolar.
 */
const CACHE_VERSION = 'v1';
const STATIC_CACHE = `ref-static-${CACHE_VERSION}`;
const PAGE_CACHE = `ref-pages-${CACHE_VERSION}`;

const STATIC_PRECACHE = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/dynamic-styles.js',
  '/static/favicon.svg',
  '/static/logo_escola_naval.jpg',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== PAGE_CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith('/static/');
}

function isPageNavigation(request) {
  return request.mode === 'navigate'
    || (request.method === 'GET'
        && request.headers.get('accept')
        && request.headers.get('accept').includes('text/html'));
}

function isCacheableGet(request) {
  if (request.method !== 'GET') return false;
  const url = new URL(request.url);
  // Nunca cachear APIs, auth, admin ou endpoints de mutação
  if (url.pathname.startsWith('/api/')) return false;
  if (url.pathname.startsWith('/auth/')) return false;
  if (url.pathname.startsWith('/admin/')) return false;
  return true;
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (!isCacheableGet(req)) return;
  const url = new URL(req.url);

  if (isStaticAsset(url)) {
    // stale-while-revalidate
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const network = fetch(req).then((res) => {
          if (res && res.status === 200) cache.put(req, res.clone());
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  if (isPageNavigation(req)) {
    // network-first
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.status === 200) {
          const copy = res.clone();
          caches.open(PAGE_CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req).then((cached) => cached
        || new Response(
          '<h1>Sem ligação</h1><p>Esta página não está disponível offline.</p>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 }
        )
      ))
    );
  }
});
