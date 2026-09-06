// Deterministic demo fixtures, used when the CAD API is unreachable (fail-soft).
// Mirror of services/mod-police-cad semantics: tenant geofences, vigilante
// agencies, biometric ghost-worker control, hash-chained trust-fund audit feed.

import type {
  CameraStream,
  Incident,
  StateId,
  TrustFundEntry,
  Unit,
} from './types';

// Approximate geofence bounding boxes (min_lat, min_lon, max_lat, max_lon),
// reference-grade copy of STATE_GEOFENCES in mod-police-cad app/domain.py.
export const STATE_GEOFENCES: Record<StateId, [number, number, number, number]> = {
  lagos: [6.35, 2.65, 6.75, 4.35],
  ogun: [6.3, 2.65, 7.35, 4.6],
  osun: [7.05, 4.0, 8.1, 5.1],
  benue: [6.35, 7.5, 8.2, 10.0],
  nasarawa: [7.45, 7.1, 9.3, 9.6],
  taraba: [6.4, 9.4, 9.6, 11.6],
};

const AGENCY_BY_STATE: Record<StateId, string> = {
  lagos: 'LNSC',
  ogun: 'SO-SAFE',
  osun: 'AMOTEKUN',
  benue: 'BSCPG',
  nasarawa: 'NPF',
  taraba: 'NPF',
};

export const DEMO_EPOCH = Date.parse('2025-01-06T08:00:00.000Z');

/** Deterministic pseudo-random in [0,1) from a string seed. */
function rand(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

function jitter(stateId: StateId, key: string, spanLat: number, spanLon: number): [number, number] {
  const [minLat, minLon] = STATE_GEOFENCES[stateId];
  return [minLat + rand(`${stateId}:${key}:lat`) * spanLat, minLon + rand(`${stateId}:${key}:lon`) * spanLon];
}

function iso(offsetMin: number): string {
  return new Date(DEMO_EPOCH + offsetMin * 60_000).toISOString();
}

/** Deterministic FNV-1a hex digest — demo stand-in for the ledger hash chain. */
export function chainHash(input: string): string {
  let h = 2166136261;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

const CATEGORIES = ['robbery', 'medical', 'fire', 'disturbance', 'accident'] as const;
const PRIORITIES = ['P1', 'P2', 'P3'] as const;

export function fixtureIncidents(stateId: StateId): Incident[] {
  const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
  const agency = AGENCY_BY_STATE[stateId];
  // Report ages (minutes before DEMO_EPOCH) and dispatch latencies are fixed so
  // the p95-vs-SLO banner is deterministic in demo mode.
  const seeds: { age: number; latency?: number }[] = [
    { age: 190, latency: 18 },
    { age: 150, latency: 24 },
    { age: 120, latency: 41 },
    { age: 90, latency: 12 },
    { age: 60, latency: 22 },
    { age: 35 },
    { age: 12 },
    { age: 4 },
  ];
  return seeds.map((s, i) => {
    const [lat, lon] = jitter(stateId, `inc${i}`, maxLat - minLat, maxLon - minLon);
    const reported = iso(-s.age);
    const dispatched = s.latency !== undefined;
    return {
      incident_id: `INC-${stateId.slice(0, 2).toUpperCase()}-${String(i + 1).padStart(3, '0')}`,
      tenant_state_id: stateId,
      agency,
      category: CATEGORIES[i % CATEGORIES.length],
      priority: PRIORITIES[i % PRIORITIES.length],
      latitude: lat,
      longitude: lon,
      status: dispatched ? 'dispatched' : 'open',
      reported_at: reported,
      assigned_unit_id: dispatched ? `UNIT-${stateId.slice(0, 2).toUpperCase()}-${(i % 4) + 1}` : undefined,
      dispatch_latency_s: s.latency,
    };
  });
}

const UNIT_STATUSES = ['available', 'enroute', 'on-scene', 'available', 'enroute', 'available'] as const;

export function fixtureUnits(stateId: StateId): Unit[] {
  const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
  const agency = AGENCY_BY_STATE[stateId];
  return UNIT_STATUSES.map((status, i) => {
    const [lat, lon] = jitter(stateId, `unit${i}`, maxLat - minLat, maxLon - minLon);
    return {
      unit_id: `UNIT-${stateId.slice(0, 2).toUpperCase()}-${i + 1}`,
      tenant_state_id: stateId,
      agency,
      call_sign: `${agency.slice(0, 3)}-${stateId.slice(0, 2).toUpperCase()}${i + 1}`,
      status,
      personnel_count: 3 + ((i * 2) % 6),
      biometric_enrolled: i % 4 !== 3, // one un-enrolled unit per state (ghost-worker flag)
      latitude: lat,
      longitude: lon,
      registered_at: iso(-24 * 60 * (30 + i)),
    };
  });
}

export function fixtureStreams(stateId: StateId): CameraStream[] {
  const [minLat, minLon, maxLat, maxLon] = STATE_GEOFENCES[stateId];
  const kinds: CameraStream['kind'][] = ['cctv', 'cctv', 'drone', 'cctv'];
  return kinds.map((kind, i) => {
    const [lat, lon] = jitter(stateId, `cam${i}`, maxLat - minLat, maxLon - minLon);
    return {
      stream_id: `STR-${stateId.slice(0, 2).toUpperCase()}-${i + 1}`,
      tenant_state_id: stateId,
      kind,
      label:
        kind === 'drone'
          ? `Drone patrol ${i + 1} — ${stateId}`
          : `CCTV junction ${i + 1} — ${stateId}`,
      latitude: lat,
      longitude: lon,
      online: i !== 2, // drone offline in demo mode
    };
  });
}

export function fixtureTrustFund(stateId: StateId): TrustFundEntry[] {
  const seeds: [TrustFundEntry['kind'], string, number, number][] = [
    ['donation', 'Dangote Foundation', 250_000_000_00, -20 * 24 * 60],
    ['donation', 'State Security Levy Q1', 120_500_000_00, -14 * 24 * 60],
    ['disbursement', 'Patrol vehicle maintenance', 18_750_000_00, -9 * 24 * 60],
    ['donation', 'Market Association levy', 42_000_000_00, -5 * 24 * 60],
    ['disbursement', 'CCTV junction cameras (phase 2)', 66_200_000_00, -2 * 24 * 60],
    ['disbursement', 'Responder stipends (biometric-verified)', 9_400_000_00, -360],
  ];
  let prev = 'GENESIS';
  return seeds.map(([kind, ref, amount, atMin], i) => {
    const at = iso(atMin);
    const entry_id = `TF-${stateId.slice(0, 2).toUpperCase()}-${String(i + 1).padStart(3, '0')}`;
    const hash = chainHash(`${prev}|${entry_id}|${kind}|${ref}|${amount}|${at}`);
    const entry: TrustFundEntry = {
      entry_id,
      tenant_state_id: stateId,
      kind,
      ref,
      amount_kobo: amount,
      at,
      prev_hash: prev,
      hash,
    };
    prev = hash;
    return entry;
  });
}

/** Verify a hash chain end-to-end; returns index of first bad link or -1. */
export function verifyTrustFundChain(entries: TrustFundEntry[]): number {
  let prev = 'GENESIS';
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    const expect = chainHash(`${prev}|${e.entry_id}|${e.kind}|${e.ref}|${e.amount_kobo}|${e.at}`);
    if (e.prev_hash !== prev || e.hash !== expect) return i;
    prev = e.hash;
  }
  return -1;
}

// Minimal GeoJSON shapes (avoids a @types/geojson dependency).
export interface GeoJsonPointFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: Record<string, string>;
}
export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection';
  features: GeoJsonPointFeature[];
}

/** GeoJSON FeatureCollection of incidents + units, for the map panel. */
export function fixtureGeoJson(stateId: StateId): GeoJsonFeatureCollection {
  const features: GeoJsonPointFeature[] = [
    ...fixtureIncidents(stateId).map((inc) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [inc.longitude, inc.latitude] as [number, number] },
      properties: { layer: 'incident', id: inc.incident_id, status: inc.status, priority: inc.priority } as Record<string, string>,
    })),
    ...fixtureUnits(stateId).map((u) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [u.longitude, u.latitude] as [number, number] },
      properties: { layer: 'unit', id: u.unit_id, status: u.status } as Record<string, string>,
    })),
  ];
  return { type: 'FeatureCollection', features };
}
