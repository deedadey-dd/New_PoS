const CACHE_NAME = 'pos-app-v1';
const STATIC_CACHE = 'pos-static-v1';
const API_CACHE = 'pos-api-v1';

const ASSETS_TO_CACHE = [
    '/',
    '/offline/',
    '/static/js/app.js',
    '/static/js/db.js',
    '/static/js/vendor/dexie.js', // Assuming this path
    '/static/js/unified-sync-manager.js',
    '/static/css/styles.css', // Check actual css path
    '/static/images/logo.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME && cacheName !== STATIC_CACHE && cacheName !== API_CACHE) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // API Strategy: Network First, then specific handling
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    // Clone and cache if needed, or just return
                    // For API, we mostly rely on IndexedDB logic, but simple cache for reads checks
                    if (event.request.method === 'GET') {
                        const responseClone = response.clone();
                        caches.open(API_CACHE).then((cache) => {
                            cache.put(event.request, responseClone);
                        });
                    }
                    return response;
                })
                .catch(() => {
                    return caches.match(event.request);
                })
        );
        return;
    }

    // Static Assets: Cache First
    if (url.pathname.startsWith('/static/') || url.pathname.match(/\.(js|css|png|jpg|svg)$/)) {
        event.respondWith(
            caches.match(event.request).then((response) => {
                return response || fetch(event.request).then((fetchRes) => {
                    const resClone = fetchRes.clone();
                    caches.open(STATIC_CACHE).then((cache) => {
                        cache.put(event.request, resClone);
                    });
                    return fetchRes;
                });
            })
        );
        return;
    }

    // HTML Pages: Network First, fallback to Offline
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    // Cache successful navigation responses (pages)
                    if (!response || response.status !== 200 || response.type !== 'basic') {
                        return response;
                    }
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseToCache);
                    });
                    return response;
                })
                .catch(() => {
                    return caches.match(event.request)
                        .then((response) => {
                            if (response) return response;
                            return caches.match('/offline/');
                        });
                })
        );
        return;
    }

    event.respondWith(
        caches.match(event.request).then((response) => {
            return response || fetch(event.request);
        })
    );
});
