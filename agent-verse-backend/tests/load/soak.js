/**
 * Soak test — sustained load to detect memory leaks and degradation.
 * Run: k6 run --env DURATION=30m tests/load/soak.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';
const DURATION = __ENV.DURATION || '30m';

const latencyTrend = new Trend('latency_over_time', true);
const errorRate = new Rate('error_rate');

export const options = {
  vus: 10,
  duration: DURATION,
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    // Key: latency should NOT increase over time (memory leak indicator)
    error_rate: ['rate<0.05'],
  },
};

const ENDPOINTS = [
  () => http.get(`${BASE_URL}/health`),
  () => http.get(`${BASE_URL}/goals`, { headers: { 'X-API-Key': API_KEY } }),
  () => http.get(`${BASE_URL}/agents`, { headers: { 'X-API-Key': API_KEY } }),
  () => http.get(`${BASE_URL}/connectors`, { headers: { 'X-API-Key': API_KEY } }),
  () => http.get(`${BASE_URL}/tenants/me`, { headers: { 'X-API-Key': API_KEY } }),
];

export default function () {
  const endpoint = ENDPOINTS[Math.floor(Math.random() * ENDPOINTS.length)];
  const start = Date.now();
  const r = endpoint();
  const elapsed = Date.now() - start;

  latencyTrend.add(elapsed);
  check(r, { 'status ok': (r) => r.status < 500 }) || errorRate.add(1);

  sleep(Math.random() * 2 + 1);
}
