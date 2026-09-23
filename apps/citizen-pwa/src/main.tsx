import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { AppStoreProvider } from './lib/store';
import './styles.css';

// Push-notification seam: when the backend push gateway lands, request
// permission here and POST the subscription to the portal. Kept inert until
// then so the app never nags for permission unprompted.
export async function registerPushSeam(): Promise<void> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  // const reg = await navigator.serviceWorker.ready;
  // const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, ... });
  // await fetch(`${API}/citizen/v1/push-subscriptions`, { method: 'POST', body: JSON.stringify(sub) });
}

// Preconnect/dns-prefetch to the API origin when it is cross-origin
// (VITE_API_BASE_URL set). Same-origin deployments need no hint — the
// connection is already warm from the document fetch. Injected at runtime so
// the hint only appears when an API origin is actually configured.
const apiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
if (apiBase) {
  try {
    const origin = new URL(apiBase, window.location.href).origin;
    if (origin !== window.location.origin) {
      for (const rel of ['preconnect', 'dns-prefetch']) {
        const link = document.createElement('link');
        link.rel = rel;
        link.href = origin;
        if (rel === 'preconnect') link.crossOrigin = '';
        document.head.appendChild(link);
      }
    }
  } catch {
    // malformed VITE_API_BASE_URL — skip hints, api.ts will surface the error
  }
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppStoreProvider>
      <App />
    </AppStoreProvider>
  </React.StrictMode>,
);
