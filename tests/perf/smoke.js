// k6 smoke spec — health-endpoint latency across key SOS services.
// Purpose: fast local regression gate (NOT the procurement stress gate —
// see tests/load/k6-ledger-split.js). Run against `docker compose up` in
// deploy/:  k6 run tests/perf/smoke.js
// Thresholds: p95 < 500ms per endpoint locally, zero failed requests.

import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

export const options = {
  scenarios: {
    health_smoke: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate==0'],
  },
};

const HOST = __ENV.SOS_HOST || 'http://localhost';

// Key services: host port -> health path (ports per deploy/docker-compose.yml).
const TARGETS = {
  'mod-rev-core': `${HOST}:8080/healthz`,
  'mod-citizen-portal': `${HOST}:8011/healthz`,
  'mod-mobility-switch': `${HOST}:8026/healthz`,
  'mod-transparency': `${HOST}:8015/healthz`,
  'control-plane': `${HOST}:8024/healthz`,
};

const trends = Object.fromEntries(
  Object.keys(TARGETS).map((name) => [name, new Trend(`latency_${name.replaceAll('-', '_')}`, true)]),
);

export default function () {
  for (const [name, url] of Object.entries(TARGETS)) {
    const res = http.get(url, { tags: { service: name } });
    trends[name].add(res.timings.duration);
    check(res, {
      [`${name} healthy`]: (r) => r.status === 200,
    });
  }
}
