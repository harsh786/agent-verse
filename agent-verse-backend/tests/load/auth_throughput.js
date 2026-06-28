/**
 * Auth throughput — hammers the API key resolution path.
 * Tests TenantMiddleware performance under load.
 * Run: k6 run tests/load/auth_throughput.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';

const authLatency = new Trend('auth_latency', true);
const errorRate = new Rate('error_rate');

export const options = {
  vus: 50,
  duration: '3m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    auth_latency: ['p(99)<100'],   // 99th percentile < 100ms for auth
    error_rate: ['rate<0.05'],
  },
};

export default function () {
  const start = Date.now();
  const r = http.get(`${BASE_URL}/tenants/me`, {
    headers: { 'X-API-Key': API_KEY },
  });
  authLatency.add(Date.now() - start);

  check(r, {
    'auth returns 200 or 401': (r) => [200, 401, 403].includes(r.status),
    'auth latency < 200ms': () => (Date.now() - start) < 200,
  }) || errorRate.add(1);

  sleep(0.1); // 100ms think time = ~10 RPS per VU
}
