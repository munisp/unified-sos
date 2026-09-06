// Dispatch-latency SLO helpers (acceptance target: dispatch latency < 30 s).

import type { Incident } from './types';
import { DISPATCH_SLO_S } from './types';

/** Elapsed seconds since an incident was reported, relative to `now`. */
export function elapsedSeconds(incident: Incident, now: number): number {
  return Math.max(0, Math.floor((now - Date.parse(incident.reported_at)) / 1000));
}

/** Effective dispatch latency for SLO accounting: actual once dispatched,
 *  otherwise the still-running elapsed wait. */
export function latencySeconds(incident: Incident, now: number): number {
  return incident.dispatch_latency_s ?? elapsedSeconds(incident, now);
}

/** Nearest-rank p95 over a sample; 0 for empty input. */
export function p95(samples: number[]): number {
  if (samples.length === 0) return 0;
  const sorted = [...samples].sort((a, b) => a - b);
  const rank = Math.max(1, Math.ceil(0.95 * sorted.length));
  return sorted[rank - 1];
}

export interface SloVerdict {
  p95Seconds: number;
  breached: boolean;
  sampleSize: number;
}

/** p95 of dispatch latencies across the queue vs the 30 s SLO. */
export function sloVerdict(incidents: Incident[], now: number): SloVerdict {
  const samples = incidents
    .filter((i) => i.status !== 'closed')
    .map((i) => latencySeconds(i, now));
  const value = p95(samples);
  return { p95Seconds: value, breached: value > DISPATCH_SLO_S, sampleSize: samples.length };
}
