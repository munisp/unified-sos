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

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppStoreProvider>
      <App />
    </AppStoreProvider>
  </React.StrictMode>,
);
