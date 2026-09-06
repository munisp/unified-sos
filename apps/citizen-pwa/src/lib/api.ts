// API client for mod-citizen-portal / mod-transparency / mod-gis-lands.
// Fail-soft: when the API is unreachable the client flips to demo mode and
// serves deterministic fixtures; mutating calls go to the offline queue.

import {
  fixtureAuditDigests,
  fixtureAuditVerification,
  fixtureCatalog,
  fixtureEscrowStatements,
  fixtureTrustFundFeed,
  fixtureWallet,
} from './fixtures';
import { enqueue } from './offlineQueue';
import type {
  ConcessionEscrowStatement,
  DeedVerification,
  PaymentQuote,
  ProcurementAuditDigest,
  ProcurementAuditVerification,
  ServiceCatalogEntry,
  ServiceRequest,
  ServiceRequestCreate,
  StateId,
  TrustFundFeed,
  WalletRead,
} from './types';

const BASE_URL: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';
const TIMEOUT_MS = 6_000;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
  }
}

let demoMode = !BASE_URL;
const listeners = new Set<(demo: boolean) => void>();

export function isDemoMode(): boolean {
  return demoMode;
}

export function onDemoModeChange(fn: (demo: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setDemoMode(v: boolean): void {
  if (demoMode !== v) {
    demoMode = v;
    listeners.forEach((fn) => fn(v));
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!BASE_URL) throw new ApiError('no-api-configured');
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
      signal: ctrl.signal,
    });
    if (!res.ok) {
      if (res.status === 404) throw new ApiError('Not found', 404);
      throw new ApiError(`Request failed (${res.status})`, res.status);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

async function readOrFixture<T>(path: string, fixture: () => T): Promise<{ data: T; demo: boolean }> {
  try {
    const data = await request<T>(path);
    setDemoMode(false);
    return { data, demo: false };
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) throw err;
    setDemoMode(true);
    return { data: fixture(), demo: true };
  }
}

// --- mod-citizen-portal ---
export async function listServices(stateId: StateId) {
  return readOrFixture(`/citizen/v1/services?state_id=${stateId}`, () => fixtureCatalog(stateId));
}

export async function createWallet(stateId: StateId, nin: string): Promise<WalletRead> {
  try {
    const w = await request<WalletRead>('/citizen/v1/wallets', {
      method: 'POST',
      body: JSON.stringify({ state_id: stateId, nin }),
    });
    setDemoMode(false);
    return w;
  } catch {
    setDemoMode(true);
    return fixtureWallet(stateId);
  }
}

export async function getWallet(stateId: StateId, walletId: string) {
  return readOrFixture(`/citizen/v1/wallets/${walletId}?state_id=${stateId}`, () =>
    fixtureWallet(stateId),
  );
}

export interface SubmitResult {
  request: ServiceRequest;
  queued: boolean;
}

export async function submitServiceRequest(
  payload: ServiceRequestCreate,
  opts: { forceQueue?: boolean } = {},
): Promise<SubmitResult> {
  const offline = typeof navigator !== 'undefined' && !navigator.onLine;
  if (!offline && !opts.forceQueue) {
    try {
      const req = await request<ServiceRequest>('/citizen/v1/service-requests', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      setDemoMode(false);
      return { request: req, queued: false };
    } catch (err) {
      if (err instanceof ApiError && err.status && err.status < 500) throw err;
      // fall through to queue on network/5xx
    }
  }
  const q = enqueue({
    url: '/citizen/v1/service-requests',
    method: 'POST',
    body: payload,
  });
  const placeholder: ServiceRequest = {
    request_id: `LOCAL-${q.queue_id}`,
    state_id: payload.state_id,
    wallet_id: payload.wallet_id,
    service_code: payload.service_code,
    form_payload: payload.form_payload ?? {},
    priority: payload.priority ?? 'STANDARD',
    fee_kobo: 0,
    status: 'SUBMITTED',
    timeline: [
      { status: 'QUEUED_OFFLINE', at: q.enqueued_at, note: 'Will sync when connectivity returns.' },
    ],
    created_at: q.enqueued_at,
  };
  return { request: placeholder, queued: true };
}

export async function getServiceRequest(stateId: StateId, requestId: string) {
  return request<ServiceRequest>(`/citizen/v1/service-requests/${requestId}?state_id=${stateId}`);
}

// --- mod-transparency (public, unauthenticated) ---
export async function trustFundFeed(stateId: StateId) {
  return readOrFixture<TrustFundFeed>(`/transparency/v1/${stateId}/trust-fund/feed`, () =>
    fixtureTrustFundFeed(stateId),
  );
}

export async function escrowStatements(stateId: StateId) {
  return readOrFixture<ConcessionEscrowStatement[]>(
    `/transparency/v1/${stateId}/escrow/statements`,
    fixtureEscrowStatements,
  );
}

export async function procurementAudit(stateId: StateId) {
  return readOrFixture<ProcurementAuditDigest[]>(
    `/transparency/v1/${stateId}/procurement/audit`,
    fixtureAuditDigests,
  );
}

export async function procurementAuditVerify(stateId: StateId) {
  return readOrFixture<ProcurementAuditVerification>(
    `/transparency/v1/${stateId}/procurement/audit/verify`,
    () => fixtureAuditVerification(stateId),
  );
}

// --- mod-gis-lands deed verification ---
export async function verifyDeed(
  stateId: StateId,
  cOfONumber: string,
  parcelUin?: string,
): Promise<DeedVerification> {
  try {
    return await request<DeedVerification>(`/api/v1/states/${stateId}/cadastre/deeds/verify`, {
      method: 'POST',
      body: JSON.stringify({ c_of_o_number: cOfONumber, parcel_uin: parcelUin }),
    });
  } catch {
    setDemoMode(true);
    // Deterministic demo verdict: numbers ending in an even digit verify.
    const valid = /\d*[02468]$/.test(cOfONumber.trim());
    return {
      c_of_o_number: cOfONumber,
      parcel_uin: parcelUin,
      valid,
      title_type: valid ? 'C_OF_O' : undefined,
      signature_chain_valid: valid,
      detail: valid
        ? 'Deed signature chain verified against the state cadastre (demo).'
        : 'No matching deed found in the state cadastre (demo).',
    };
  }
}

// --- FSPIOP payment stub (quote flow seam) ---
export async function requestPaymentQuote(ticketRef: string, amountKobo: number): Promise<PaymentQuote> {
  try {
    return await request<PaymentQuote>('/payments/v1/quotes', {
      method: 'POST',
      body: JSON.stringify({ ticket_ref: ticketRef, amount_kobo: amountKobo }),
    });
  } catch {
    setDemoMode(true);
    return {
      quote_id: `QTE-DEMO-${ticketRef.slice(-6).toUpperCase()}`,
      ticket_ref: ticketRef,
      amount_kobo: amountKobo,
      payee_fsp: 'demo-fsp',
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      status: 'QUOTED',
    };
  }
}

export async function confirmPayment(quote: PaymentQuote): Promise<PaymentQuote> {
  try {
    return await request<PaymentQuote>(`/payments/v1/quotes/${quote.quote_id}/confirm`, {
      method: 'POST',
    });
  } catch {
    setDemoMode(true);
    return { ...quote, status: 'COMPLETED' };
  }
}
