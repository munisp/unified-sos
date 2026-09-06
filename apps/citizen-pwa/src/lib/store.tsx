// Lightweight app store: selected state, wallet handle, and locally tracked
// service requests (synced when online).

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { ServiceRequest, StateId, WalletRead } from './types';

const STATE_KEY = 'sos-citizen:state';
const WALLET_KEY = 'sos-citizen:wallet';
const REQUESTS_KEY = 'sos-citizen:requests';

interface AppStore {
  stateId: StateId | null;
  selectState: (s: StateId) => void;
  clearState: () => void;
  wallet: WalletRead | null;
  setWallet: (w: WalletRead) => void;
  requests: ServiceRequest[];
  trackRequest: (r: ServiceRequest) => void;
  updateRequest: (r: ServiceRequest) => void;
}

const Ctx = createContext<AppStore | null>(null);

function readJson<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function AppStoreProvider({ children }: { children: ReactNode }) {
  const [stateId, setStateId] = useState<StateId | null>(() => readJson<StateId>(STATE_KEY));
  const [wallet, setWalletState] = useState<WalletRead | null>(() => readJson<WalletRead>(WALLET_KEY));
  const [requests, setRequests] = useState<ServiceRequest[]>(() => readJson<ServiceRequest[]>(REQUESTS_KEY) ?? []);

  useEffect(() => {
    if (stateId) window.localStorage.setItem(STATE_KEY, JSON.stringify(stateId));
  }, [stateId]);
  useEffect(() => {
    if (wallet) window.localStorage.setItem(WALLET_KEY, JSON.stringify(wallet));
  }, [wallet]);
  useEffect(() => {
    window.localStorage.setItem(REQUESTS_KEY, JSON.stringify(requests));
  }, [requests]);

  const selectState = useCallback((s: StateId) => setStateId(s), []);
  const clearState = useCallback(() => {
    setStateId(null);
    window.localStorage.removeItem(STATE_KEY);
  }, []);
  const setWallet = useCallback((w: WalletRead) => setWalletState(w), []);

  const trackRequest = useCallback((r: ServiceRequest) => {
    setRequests((prev) => [r, ...prev.filter((x) => x.request_id !== r.request_id)]);
  }, []);
  const updateRequest = useCallback((r: ServiceRequest) => {
    setRequests((prev) => prev.map((x) => (x.request_id === r.request_id ? r : x)));
  }, []);

  const value = useMemo(
    () => ({ stateId, selectState, clearState, wallet, setWallet, requests, trackRequest, updateRequest }),
    [stateId, selectState, clearState, wallet, setWallet, requests, trackRequest, updateRequest],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppStore(): AppStore {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAppStore must be used inside AppStoreProvider');
  return ctx;
}
