// k6 stress spec — Stage 2 Performance & Stress Testing
// Target: TigerBeetle statutory split path via APISIX gateway.
// Gates: 50,000 concurrent API req/s; p99 end-to-end < 50ms; zero 5xx.
// Ledger-side target (service-level benchmark): 500,000 transfers/sec with
// zero balance divergence (verified by TigerBeetle benchmark simulator).

import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    split_storm: {
      executor: 'ramping-arrival-rate',
      startRate: 1000,
      timeUnit: '1s',
      preAllocatedVUs: 5000,
      maxVUs: 60000,
      stages: [
        { target: 10000, duration: '2m' },
        { target: 50000, duration: '5m' },   // procurement gate load
        { target: 50000, duration: '10m' },  // sustained
        { target: 0, duration: '2m' },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(99)<50'],
    http_req_failed: ['rate==0'],
  },
};

const BASE = __ENV.SOS_BASE_URL || 'https://api.nasarawa.gov.ng/sos';
const TOKEN = __ENV.SOS_JWT; // Keycloak realm token (never hard-code)

export default function () {
  const payload = JSON.stringify({
    taxpayer_stin: 'NG-NAS-2026-892104',
    mda_code: 'MDA-BIR-001',
    revenue_head: 'REV_DIRECT_ASSESSMENT',
    tax_period_year: 2026,
    gross_income_kobo: 1200000000,
    allowable_deductions_kobo: 240000000,
    calculated_tax_kobo: 192000000,
    metadata: { lga_code: 'LGA-KARU', assessment_officer_id: 'USR-OFF-410' },
  });

  const res = http.post(`${BASE}/api/v1/states/nasarawa/revenue/assessments`, payload, {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
  });

  check(res, {
    'assessment created': (r) => r.status === 201,
    'split pending id present': (r) => r.json('tigerbeetle_transfer_pending_id') !== undefined,
  });
}
