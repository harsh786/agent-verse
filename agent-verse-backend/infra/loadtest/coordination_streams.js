import http from 'k6/http';
import { check } from 'k6';

const base = __ENV.BASE_URL || 'http://localhost:8000';
const tenant = __ENV.PROGRAM13_TEST_TENANT;
if (!tenant || !__ENV.API_KEY || !__ENV.SESSION_ID) throw new Error('PROGRAM13_TEST_TENANT, API_KEY, and SESSION_ID are required');
export const options = { thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<100'] } };

export default function () {
  const response = http.get(`${base}/api/v1/coordination/sessions/${__ENV.SESSION_ID}/events?after_sequence=0`, {
    headers: { 'X-API-Key': __ENV.API_KEY, 'Last-Event-ID': '0', 'Idempotency-Key': `stream-${tenant}` },
    timeout: '10s', tags: { workload: 'coordination-stream' },
  });
  check(response, { 'stream accepted': (r) => r.status === 200, 'SSE response': (r) => String(r.headers['Content-Type']).includes('text/event-stream') });
}

// Cleanup is read-only: the test session is removed by the certification fixture.
