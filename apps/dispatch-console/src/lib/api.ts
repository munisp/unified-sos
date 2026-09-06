// API client for mod-police-cad (112 emergency CAD).
// Fail-soft: when the API is unreachable the client flips to demo mode and
// serves deterministic fixtures; mutating calls are applied to local state.

import {
  fixtureIncidents,
  fixtureStreams,
  fixtureTrustFund,
  fixtureUnits,
} from './fixtures';
import type {
  CameraStream,
  DispatchEvent,
  Incident,
  StateId,
  StreamSession,
  TrustFundEntry,
  Unit,
} from './types';

const BASE_URL: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';
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

// --- incident queue ---
export async function listIncidents(stateId: StateId) {
  return readOrFixture(`/cad/v1/incidents?tenant_state_id=${stateId}`, () =>
    fixtureIncidents(stateId),
  );
}

// --- unit roster ---
export async function listUnits(stateId: StateId) {
  return readOrFixture(`/cad/v1/units?tenant_state_id=${stateId}`, () => fixtureUnits(stateId));
}

// --- dispatch ---
export async function dispatchUnit(
  incident: Incident,
  unit: Unit,
): Promise<{ event: DispatchEvent; demo: boolean }> {
  try {
    const event = await request<DispatchEvent>('/cad/v1/dispatch', {
      method: 'POST',
      body: JSON.stringify({
        incident_id: incident.incident_id,
        unit_id: unit.unit_id,
        latitude: incident.latitude,
        longitude: incident.longitude,
      }),
    });
    setDemoMode(false);
    return { event, demo: false };
  } catch {
    setDemoMode(true);
    const now = new Date().toISOString();
    return {
      event: {
        event_id: `EVT-${incident.incident_id}`,
        incident_id: incident.incident_id,
        unit_id: unit.unit_id,
        latitude: incident.latitude,
        longitude: incident.longitude,
        dispatched_at: now,
        geofence_verified: true,
      },
      demo: true,
    };
  }
}

// --- live streams (WebRTC session hookup placeholder) ---
export async function listStreams(stateId: StateId) {
  return readOrFixture(`/cad/v1/streams?tenant_state_id=${stateId}`, () => fixtureStreams(stateId));
}

export async function openStreamSession(stream: CameraStream): Promise<StreamSession> {
  try {
    return await request<StreamSession>(`/cad/v1/streams/${stream.stream_id}/session`, {
      method: 'POST',
      body: JSON.stringify({ tenant_state_id: stream.tenant_state_id }),
    });
  } catch {
    setDemoMode(true);
    return {
      session_id: `SES-${stream.stream_id}`,
      stream_id: stream.stream_id,
      offer_url: `/cad/v1/streams/${stream.stream_id}/session`,
      ice_servers: [],
    };
  }
}

// --- trust-fund transparency ---
export async function trustFundAuditFeed(stateId: StateId) {
  return readOrFixture<TrustFundEntry[]>(`/cad/v1/trust-fund/${stateId}/audit-feed`, () =>
    fixtureTrustFund(stateId),
  );
}
