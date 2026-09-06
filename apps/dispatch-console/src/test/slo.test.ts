import { describe, expect, it } from 'vitest';

import { DEMO_EPOCH, fixtureIncidents } from '../lib/fixtures';
import { latencySeconds, p95, sloVerdict } from '../lib/slo';
import { DISPATCH_SLO_S, type Incident } from '../lib/types';

function makeIncident(over: Partial<Incident>): Incident {
  return {
    incident_id: 'INC-T',
    tenant_state_id: 'lagos',
    agency: 'LNSC',
    category: 'robbery',
    priority: 'P2',
    latitude: 6.5,
    longitude: 3.4,
    status: 'open',
    reported_at: new Date(DEMO_EPOCH).toISOString(),
    ...over,
  };
}

describe('p95 (nearest-rank)', () => {
  it('returns 0 for empty samples', () => {
    expect(p95([])).toBe(0);
  });

  it('picks the nearest-rank value', () => {
    expect(p95([5, 10, 15, 20, 25, 30, 35, 40, 45, 50])).toBe(50);
    expect(p95([7, 3, 9])).toBe(9);
    expect(p95([12])).toBe(12);
  });
});

describe('sloVerdict', () => {
  it('flags breach when p95 exceeds the 30s SLO', () => {
    // fixture latencies: 18,24,41,12,22 + open ages 35m/12m/4m → p95 well over 30
    const verdict = sloVerdict(fixtureIncidents('lagos'), DEMO_EPOCH);
    expect(verdict.breached).toBe(true);
    expect(verdict.p95Seconds).toBeGreaterThan(DISPATCH_SLO_S);
  });

  it('stays green when all latencies are within SLO', () => {
    const incidents = [12, 18, 22, 25].map((s, i) =>
      makeIncident({ incident_id: `I${i}`, status: 'dispatched', dispatch_latency_s: s }),
    );
    const verdict = sloVerdict(incidents, DEMO_EPOCH);
    expect(verdict.breached).toBe(false);
    expect(verdict.p95Seconds).toBe(25);
  });

  it('excludes closed incidents and counts running elapsed time for open ones', () => {
    const incidents = [
      makeIncident({ incident_id: 'A', status: 'closed', dispatch_latency_s: 500 }),
      makeIncident({ incident_id: 'B', status: 'open', reported_at: new Date(DEMO_EPOCH - 10_000).toISOString() }),
    ];
    const verdict = sloVerdict(incidents, DEMO_EPOCH);
    expect(verdict.sampleSize).toBe(1);
    expect(verdict.p95Seconds).toBe(10);
    expect(verdict.breached).toBe(false);
  });

  it('uses actual latency once dispatched', () => {
    const inc = makeIncident({ status: 'dispatched', dispatch_latency_s: 27 });
    expect(latencySeconds(inc, DEMO_EPOCH + 999_000)).toBe(27);
  });
});
