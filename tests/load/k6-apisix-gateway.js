// k6 stress spec — Stage 2 Performance & Stress Testing
// Target: APISIX gateway north-south API path.
// Gate: 50,000 concurrent API req/s sustained; p99 < 100 ms gateway overhead;
// error rate == 0. Companion to k6-ledger-split.js (statutory split path).

import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    gateway_storm: {
      executor: 'ramping-arrival-rate',
      startRate: 2000,
      timeUnit: '1s',
      preAllocatedVUs: 8000,
      maxVUs: 80000,
      stages: [
        { target: 10000, duration: '2m' },   // ramp
        { target: 50000, duration: '5m' },   // procurement gate load
        { target: 50000, duration: '10m' },  // sustained gate window
        { target: 0, duration: '2m' },       // drain
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(99)<100'],
    http_req_failed: ['rate==0'],
    http_reqs: ['rate>=49000'], // fail the run if the gateway cannot hold 50k rps
  },
};

const BASE = __ENV.SOS_BASE_URL || 'https://api.nasarawa.gov.ng/sos';
const TOKEN = __ENV.SOS_JWT; // Keycloak realm token (never hard-code)

const ROUTES = [
  '/v1/revenue/assessments',
  '/v1/payments/splits',
  '/v1/gis/parcels',
  '/v1/citizen/bills',
];

export default function () {
  const route = ROUTES[Math.floor(Math.random() * ROUTES.length)];
  const res = http.get(`${BASE}${route}?limit=25`, {
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    tags: { route },
  });
  check(res, {
    'status 2xx': (r) => r.status >= 200 && r.status < 300,
    'gateway p99 budget': (r) => r.timings.duration < 100,
  });
}
