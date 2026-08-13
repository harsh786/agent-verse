import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

const base = __ENV.BASE_URL || 'http://localhost:8000';
const tenant = __ENV.PROGRAM13_TEST_TENANT;
if (!tenant || !__ENV.API_KEY) throw new Error('PROGRAM13_TEST_TENANT and API_KEY are required');
export const options = { thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<100'] } };
const headers = (key) => ({ 'X-API-Key': __ENV.API_KEY, 'Content-Type': 'application/json', 'Idempotency-Key': key });

export default function () {
  const key = `p13-${tenant}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
  const response = http.post(`${base}/api/v1/coordination/sessions`, JSON.stringify({
    civilization_id: 'load-certification', goal_id: key,
    policy_snapshot: { version: 'load-v1' }, budget_snapshot: { ceiling: 1 },
  }), { headers: headers(key), tags: { workload: 'coordination-session' } });
  check(response, { 'accepted': (r) => r.status === 201 || r.status === 202 });
}

// Cleanup is performed by the namespace-scoped certification fixture using the p13 prefix.
