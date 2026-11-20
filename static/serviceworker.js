/**
 * Service Worker for Hendaxis POS (PWA)
 * Provides offline bootstrapping, asset caching, and resilient network fallbacks.
 */

const CACHE_VERSION = 'pos-pwa-v2.1.0';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGES_CACHE = `${CACHE_VERSION}-pages`;
const CDN_CACHE = `${CACHE_VERSION}-cdn`;

// Core static assets required for POS and Dashboard to run offline
const PRECACHE_ASSETS = [
    '/offline/',
    '/sales/pos/',
    '/dashboard/',
    '/static/manifest.json',
    '/static/images/cart_icon.png',
    '/static/images/logo.png',
    '/static/images/icons/icon-192x192.png',
    '/static/images/icons/icon-512x512.png',
    '/static/css/styles.css',
    '/static/js/offline-sync.js',
    '/static/js/vendor/dexie.js',
    '/static/js/db.js',
    // Essential external CDN assets for Bootstrap & Icons
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css'
];

// Install: Pre-cache core shell resources
self.addEventListener('install', (event) => {
    console.log('[ServiceWorker] Installing version:', CACHE_VERSION);
    event.waitUntil(
        caches.open(STATIC_CACHE).then((cache) => {
            return Promise.allSettled(
                PRECACHE_ASSETS.map((url) => {
                    return fetch(url, { mode: url.startsWith('http') ? 'cors' : 'same-origin' })
                        .then((res) => {
                            if (res.ok) {
                                return cache.put(url, res);
                            }
                            console.warn(`[ServiceWorker] Precache failed for ${url}: status ${res.status}`);
                        })
                        .catch((err) => {
                            console.warn(`[ServiceWorker] Precache network error for ${url}:`, err);
                        });
                })
            );
        })
    );
    self.skipWaiting();
});

// Activate: Clean up older cache versions
self.addEventListener('activate', (event) => {
    console.log('[ServiceWorker] Activating version:', CACHE_VERSION);
    const activeCaches = [STATIC_CACHE, PAGES_CACHE, CDN_CACHE];
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (!activeCaches.includes(cacheName)) {
                        console.log('[ServiceWorker] Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

// Fetch: Strategy router
self.addEventListener('fetch', (event) => {
    const request = event.request;

    // Only intercept GET requests
    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    // 1. Bypass ServiceWorker for Admin, Sync API, and mutation endpoints
    if (
        url.pathname.startsWith('/sales/api/') ||
        url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/super_office/') ||
        url.pathname.startsWith('/admin/')
    ) {
        return;
    }

    // 2. Navigation Requests (HTML Pages)
    if (request.mode === 'navigate') {
        // POS & Dashboard Strategy: Network-First with a 2.5s timeout, falling back to cached shell
        if (url.pathname.startsWith('/sales/pos') || url.pathname === '/dashboard' || url.pathname === '/dashboard/') {
            const fallbackKey = url.pathname.startsWith('/sales/pos') ? '/sales/pos/' : '/dashboard/';
            event.respondWith(
                Promise.race([
                    fetch(request),
                    new Promise((_, reject) =>
                        setTimeout(() => reject(new Error('Network timeout for shell')), 2500)
                    )
                ])
                    .then((networkResponse) => {
                        if (networkResponse && networkResponse.status === 200) {
                            const responseClone = networkResponse.clone();
                            caches.open(PAGES_CACHE).then((cache) => {
                                cache.put(request, responseClone.clone());
                                cache.put(fallbackKey, responseClone);
                            });
                        }
                        return networkResponse;
                    })
                    .catch(() => {
                        // Offline or network timed out: Match cached page
                        return caches.match(request, { ignoreSearch: true })
                            .then((cached) => cached || caches.match(fallbackKey))
                            .then((cachedPage) => cachedPage || caches.match('/offline/'));
                    })
            );
            return;
        }

        // Other pages (e.g. inventory, reports): Network-First, fallback to /offline/
        event.respondWith(
            fetch(request).catch(() => {
                return caches.match('/offline/');
            })
        );
        return;
    }

    // 3. External CDN Assets (Bootstrap, Icons, CDN fonts)
    if (url.origin === 'https://cdn.jsdelivr.net') {
        event.respondWith(
            caches.match(request).then((cachedResponse) => {
                if (cachedResponse) {
                    return cachedResponse;
                }
                return fetch(request)
                    .then((networkResponse) => {
                        if (networkResponse && networkResponse.status === 200) {
                            const clone = networkResponse.clone();
                            caches.open(CDN_CACHE).then((cache) => cache.put(request, clone));
                        }
                        return networkResponse;
                    })
                    .catch(() => cachedResponse);
            })
        );
        return;
    }

    // 4. Local Static Assets (CSS, JS, Images, Fonts)
    if (
        url.pathname.startsWith('/static/') ||
        url.pathname.match(/\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot)$/i)
    ) {
        event.respondWith(
            caches.match(request).then((cachedResponse) => {
                const fetchPromise = fetch(request)
                    .then((networkResponse) => {
                        if (networkResponse && networkResponse.status === 200) {
                            const clone = networkResponse.clone();
                            caches.open(STATIC_CACHE).then((cache) => cache.put(request, clone));
                        }
                        return networkResponse;
                    })
                    .catch(() => cachedResponse);

                return cachedResponse || fetchPromise;
            })
        );
        return;
    }

    // 5. Default Fallback
    event.respondWith(
        caches.match(request).then((response) => response || fetch(request))
    );
});

// Client communication: Warm caches on demand or skip waiting
self.addEventListener('message', (event) => {
    if (!event.data) return;

    if (event.data.type === 'CACHE_CURRENT_PAGE' && event.data.html) {
        const headers = new Headers({ 'Content-Type': 'text/html; charset=utf-8' });
        const response = new Response(event.data.html, { headers, status: 200, statusText: 'OK' });
        caches.open(PAGES_CACHE).then((cache) => {
            cache.put('/sales/pos/', response.clone());
            if (event.data.url) {
                cache.put(event.data.url, response);
            }
        });
    }

    if (event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
