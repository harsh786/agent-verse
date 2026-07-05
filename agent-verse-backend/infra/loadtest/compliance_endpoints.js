/**
 * k6 load test for compliance endpoints (GST, DPDP, policy-rules, SLA).
 *
 * Run smoke test:
 *   k6 run --vus 10 --duration 30s infra/loadtest/compliance_endpoints.js
 *
 * Run full load:
 *   k6 run --vus 100 --duration 5m infra/loadtest/compliance_endpoints.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const policyLatency = new Trend('policy_latency', true);
const gstLatency = new Trend('gst_latency', true);

export const options = {
  stages: [
    { duration: '30s', target: 20 },   // ramp up
    { duration: '2m', target: 100 },   // sustain
    { duration: '30s', target: 0 },    // ramp down
  ],
  thresholds: {
    errors: ['rate<0.05'],             // <5% error rate
    http_req_duration: ['p(95)<500'],  // p95 < 500ms
    policy_latency: ['p(95)<200'],     // policy eval p95 < 200ms
    gst_latency: ['p(95)<300'],        // GST invoice list p95 < 300ms
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'av_free_pinelabs_dev_2026';

const headers = {
  'Content-Type': 'application/json',
  'X-API-Key': API_KEY,
};

export default function () {
  // Policy rule evaluation (hot path — before every tool call)
  const policyStart = Date.now();
  const policyResp = http.post(
    `${BASE_URL}/governance/policy-rules/evaluate`,
    JSON.stringify({ tool_name: 'jira_search', arguments: { q: 'test' } }),
    { headers }
  );
  policyLatency.add(Date.now() - policyStart);
  check(policyResp, {
    'policy eval: status 200': r => r.status === 200,
    'policy eval: has allowed field': r => JSON.parse(r.body).allowed !== undefined,
  });
  errorRate.add(policyResp.status !== 200);

  sleep(0.1);

  // GST invoice list
  const gstStart = Date.now();
  const gstResp = http.get(`${BASE_URL}/billing/gst/invoices`, { headers });
  gstLatency.add(Date.now() - gstStart);
  check(gstResp, { 'gst invoices: status 200 or 401': r => [200, 401].includes(r.status) });

  sleep(0.1);

  // DPDP grievance officer (public endpoint)
  const dpdpResp = http.get(`${BASE_URL}/compliance/dpdp/grievance-officer`, { headers });
  check(dpdpResp, { 'dpdp grievance: status 200': r => r.status === 200 });
  errorRate.add(dpdpResp.status !== 200);

  sleep(0.1);

  // SLA plan info
  const slaResp = http.get(`${BASE_URL}/sla/my-plan`, { headers });
  check(slaResp, { 'sla plan: status 200 or 401': r => [200, 401].includes(r.status) });

  sleep(0.2);

  // Public status (no auth)
  const statusResp = http.get(`${BASE_URL}/status`);
  check(statusResp, { 'public status: accessible': r => [200, 202].includes(r.status) });
  errorRate.add(![200, 202].includes(statusResp.status));

  sleep(0.5);
}
