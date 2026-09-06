import { describe, expect, it } from 'vitest';

import {
  fixtureGeoJson,
  fixtureIncidents,
  fixtureStreams,
  fixtureTrustFund,
  fixtureUnits,
  verifyTrustFundChain,
} from '../lib/fixtures';
import { STATES } from '../lib/types';

describe('tenant scoping of fixtures', () => {
  it('scopes every fixture record to the requested tenant', () => {
    for (const s of STATES) {
      expect(fixtureIncidents(s.id).every((i) => i.tenant_state_id === s.id)).toBe(true);
      expect(fixtureUnits(s.id).every((u) => u.tenant_state_id === s.id)).toBe(true);
      expect(fixtureStreams(s.id).every((c) => c.tenant_state_id === s.id)).toBe(true);
      expect(fixtureTrustFund(s.id).every((e) => e.tenant_state_id === s.id)).toBe(true);
    }
  });

  it('produces distinct ids per tenant (no cross-state collisions)', () => {
    const lagos = new Set(fixtureUnits('lagos').map((u) => u.unit_id));
    const benue = fixtureUnits('benue').map((u) => u.unit_id);
    expect(benue.some((id) => lagos.has(id))).toBe(false);
  });

  it('is deterministic', () => {
    expect(fixtureIncidents('osun')).toEqual(fixtureIncidents('osun'));
    expect(fixtureTrustFund('taraba')).toEqual(fixtureTrustFund('taraba'));
  });

  it('emits GeoJSON points for incidents and units', () => {
    const gj = fixtureGeoJson('ogun');
    expect(gj.type).toBe('FeatureCollection');
    expect(gj.features.length).toBeGreaterThan(0);
    expect(gj.features.every((f) => f.geometry.type === 'Point')).toBe(true);
    expect(gj.features.some((f) => f.properties?.layer === 'unit')).toBe(true);
    expect(gj.features.some((f) => f.properties?.layer === 'incident')).toBe(true);
  });
});

describe('trust-fund hash chain', () => {
  it('verifies intact for all tenants', () => {
    for (const s of STATES) {
      expect(verifyTrustFundChain(fixtureTrustFund(s.id))).toBe(-1);
    }
  });

  it('detects tampering', () => {
    const feed = fixtureTrustFund('lagos');
    feed[2] = { ...feed[2], amount_kobo: feed[2].amount_kobo + 1 };
    expect(verifyTrustFundChain(feed)).toBe(2);
  });
});
