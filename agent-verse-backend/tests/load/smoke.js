/**
 * Smoke test — 2 VUs, 1 minute.
 * Verifies the API is reachable and responding correctly.
 * Run: k6 run tests/load/smoke.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';

const errorRate = new Rate('error_rate');
const healthLatency = new Trend('health_latency', true);
const metricsLatency = new Trend('metrics_latency', true);

export const options = {
  vus: 2,
  duration: '1m',
  thresholds: {
    http_req_failed: ['rate<0.01'],          // <1% errors
    http_req_duration: ['p(95)<500'],         // 95th percentile < 500ms
    error_rate: ['rate<0.05'],
  },
};

export default function () {
  // Health check
  const health = http.get(`${BASE_URL}/health`);
  healthLatency.add(health.timings.duration);
  check(health, {
    'health status 200': (r) => r.status === 200,
    'health has status field': (r) => {
      try { return JSON.parse(r.body).status !== undefined; } catch { return false; }
    },
  }) || errorRate.add(1);

  // Metrics endpoint
  const metrics = http.get(`${BASE_URL}/metrics`);
  metricsLatency.add(metrics.timings.duration);
  check(metrics, {
    'metrics status 200': (r) => r.status === 200,
  }) || errorRate.add(1);

  // Auth check
  const me = http.get(`${BASE_URL}/tenants/me`, {
    headers: { 'X-API-Key': API_KEY },
  });
  check(me, {
    'tenants/me returns 200 or 401': (r) => [200, 401, 403].includes(r.status),
  }) || errorRate.add(1);

  sleep(1);
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
  };
}

function textSummary(data, opts) {
  return JSON.stringify({
    metrics: {
      http_req_duration: data.metrics.http_req_duration?.values,
      http_req_failed: data.metrics.http_req_failed?.values,
      error_rate: data.metrics.error_rate?.values,
    },
    root_group: data.root_group?.name,
  }, null, 2);
}
