/// <reference lib="webworker" />
// Service worker: precaches the app shell, serves SPA navigations offline,
// and queues failed POST submissions (service requests) in IndexedDB for
// background replay when connectivity returns.

import { precacheAndRoute, createHandlerBoundToURL } from 'workbox-precaching';
import { registerRoute, NavigationRoute } from 'workbox-routing';
import { NetworkFirst, CacheFirst } from 'workbox-strategies';

declare let self: ServiceWorkerGlobalScope;

precacheAndRoute(self.__WB_MANIFEST);

// SPA navigation fallback (offline shell).
registerRoute(new NavigationRoute(createHandlerBoundToURL('index.html')));

// Read-heavy API GETs: network-first with cache fallback.
registerRoute(
  ({ url, request }) => request.method === 'GET' && /\/(citizen|transparency|api)\/v1\//.test(url.pathname),
  new NetworkFirst({ cacheName: 'sos-api-reads', networkTimeoutSeconds: 6 }),
);

// Static assets.
registerRoute(
  ({ request }) => ['style', 'script', 'worker', 'font', 'image'].includes(request.destination),
  new CacheFirst({ cacheName: 'sos-static' }),
);

// --- Offline POST queue (IndexedDB) -----------------------------------------

const DB_NAME = 'sos-citizen-sw';
const STORE = 'post-queue';

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { autoIncrement: true });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function queuePost(url: string, body: string): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add({ url, body, enqueued_at: new Date().toISOString() });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function drainQueue(): Promise<void> {
  const db = await openDb();
  const items = await new Promise<{ key: IDBValidKey; value: { url: string; body: string } }[]>(
    (resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const store = tx.objectStore(STORE);
      const out: { key: IDBValidKey; value: { url: string; body: string } }[] = [];
      const cursorReq = store.openCursor();
      cursorReq.onsuccess = () => {
        const cursor = cursorReq.result;
        if (cursor) {
          out.push({ key: cursor.key, value: cursor.value });
          cursor.continue();
        } else resolve(out);
      };
      cursorReq.onerror = () => reject(cursorReq.error);
    },
  );
  for (const { key, value } of items) {
    try {
      const res = await fetch(value.url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: value.body,
      });
      if (!res.ok && res.status >= 500) throw new Error(`HTTP ${res.status}`);
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(key);
    } catch {
      break; // still offline — keep remaining entries for next replay
    }
  }
}

const QUEUEABLE = /\/citizen\/v1\/(service-requests|petitions)$/;

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'POST' || !QUEUEABLE.test(new URL(request.url).pathname)) return;
  event.respondWith(
    (async () => {
      const body = await request.clone().text();
      try {
        const res = await fetch(request);
        if (res.status >= 500) throw new Error(`HTTP ${res.status}`);
        return res;
      } catch {
        await queuePost(request.url, body);
        return new Response(
          JSON.stringify({ queued: true, request_id: `LOCAL-SW-${Date.now()}` }),
          { status: 202, headers: { 'content-type': 'application/json' } },
        );
      }
    })(),
  );
});

self.addEventListener('message', (event) => {
  if (event.data === 'sos:flush-queue') {
    event.waitUntil(drainQueue().then(() => {
      self.clients.matchAll().then((clients) => clients.forEach((c) => c.postMessage('sos:queue-flushed')));
    }));
  }
});

self.addEventListener('sync', ((event: Event & { tag?: string; waitUntil?: (p: Promise<unknown>) => void }) => {
  if (event.tag === 'sos-offline-queue') event.waitUntil?.(drainQueue());
}) as EventListener);

// Push seam: display notifications when the push gateway is wired up.
self.addEventListener('push', (event) => {
  const data = (event as PushEvent).data?.json() as { title?: string; body?: string } | undefined;
  (event as PushEvent).waitUntil(
    self.registration.showNotification(data?.title ?? 'SOS Citizen', {
      body: data?.body ?? '',
      icon: '/icons/icon-192.png',
    }),
  );
});
