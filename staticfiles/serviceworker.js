// Service Worker for POS System
// Provides offline caching for core app shell resources

const CACHE_NAME = 'pos-cache-v1';

// Core resources to cache for offline shell
const PRECACHE_URLS = [
    '/',
    '/static/css/styles.css',
    '/static/js/app.js',
    '/static/js/offline-sync.js',
    '/static/images/cart_icon.png',
    '/static/images/icons/icon-192x192.png',
    '/static/images/icons/icon-512x512.png',
];

// Install: cache core resources
self.addEventListener('install', (event) => {
    console.log('[ServiceWorker] Install');
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[ServiceWorker] Pre-caching app shell');
            return cache.addAll(PRECACHE_URLS);
        }).catch((err) => {
            console.warn('[ServiceWorker] Pre-cache failed (some resources may not be available):', err);
        })
    );
    self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[ServiceWorker] Activate');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((name) => name !== CACHE_NAME)
                    .map((name) => caches.delete(name))
            );
        })
    );
    self.clients.claim();
});

// Fetch: network first, fall back to cache for GET requests
self.addEventListener('fetch', (event) => {
    // Only handle GET requests
    if (event.request.method !== 'GET') return;

    // Skip API and admin requests
    const url = new URL(event.request.url);
    if (url.pathname.startsWith('/sales/api/') || 
        url.pathname.startsWith('/super_office/') ||
        url.pathname.startsWith('/admin/')) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Clone and cache successful responses for static assets
                if (response.ok && url.pathname.startsWith('/static/')) {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            })
            .catch(() => {
                // Network failed, try cache
                return caches.match(event.request).then((cached) => {
                    if (cached) return cached;
                    // For navigation requests, return cached index
                    if (event.request.mode === 'navigate') {
                        return caches.match('/');
                    }
                    return new Response('Offline', { status: 503, statusText: 'Offline' });
                });
            })
    );
});
