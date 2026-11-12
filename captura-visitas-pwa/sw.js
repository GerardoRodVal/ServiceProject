// Service worker: guarda la app para que abra sin señal.
// Cada vez que cambies index.html, sube la versión para que los celulares tomen la nueva.
const VERSION = 'visitas-v15';
// Lo indispensable: si falla uno, no se instala. Con el gestor se sirven todos; desde la nube solo estos.
const SHELL = ['./', './index.html', './manifest.webmanifest', './icon.svg', './icon-192.png', './icon-512.png', './vendor/qrcode.js'];
// Páginas que solo sirve el gestor (taller y seguimiento): se guardan si existen y no estorban si no.
const OPCIONALES = ['./taller.html', './seguimiento.html', './taller.webmanifest'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(VERSION)
      .then(cache => cache.addAll(SHELL).then(() => Promise.all(OPCIONALES.map(u => cache.add(u).catch(() => {})))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Solo se atiende lo del mismo origen: el proveedor de inicio de sesión y otros servicios van directo a la red.
  if (url.origin !== self.location.origin) return;

  // Los datos (API) nunca salen de caché: el seguimiento en tiempo real y la configuración deben ser los actuales.
  // Excepto las fotos del catálogo: su dirección lleva un identificador que cambia cuando cambia la foto, así que se
  // pueden guardar y verse sin señal una vez vistas.
  if (url.pathname.includes('/api/') && !url.pathname.startsWith('/api/imagen/')) return;

  // La configuración y el modo (gestor o nube) siempre se piden a la red para que los cambios se vean de inmediato.
  if (url.pathname.endsWith('/config.json') || url.pathname.endsWith('/app-config.json')) {
    event.respondWith(
      fetch(req).catch(() => caches.match(req, { ignoreSearch: true }))
    );
    return;
  }

  // Primero responde desde caché (abre al instante sin señal) y actualiza en segundo plano.
  event.respondWith(
    caches.match(req, { ignoreSearch: true }).then(cached => {
      const red = fetch(req)
        .then(res => {
          if (res.ok || res.type === 'opaque') {
            const copia = res.clone();
            caches.open(VERSION).then(cache => cache.put(req, copia));
          }
          return res;
        })
        .catch(() => cached);
      return cached || red;
    })
  );
});
