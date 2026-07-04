/**
 * k6 load test — goal submission
 * Target: p95 < 300 ms at 10k concurrent streams
 *
 * Usage:
 *   k6 run infra/loadtest/goal_submission.js \
 *     -e BASE_URL=http://localhost:8000 \
 *     -e API_KEY=av_free_xxx
 *
 * CI smoke (low VUs, short duration):
 *   k6 run -e SMOKE=1 -e BASE_URL=... -e API_KEY=... infra/loadtest/goal_submission.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const submitDuration = new Trend('goal_submit_duration', true);
const submitErrors = new Rate('goal_submit_errors');

const SMOKE = __ENV.SMOKE === '1';

export const options = {
  stages: SMOKE
    ? [
        { duration: '10s', target: 10 },
        { duration: '20s', target: 10 },
        { duration: '10s', target: 0 },
      ]
    : [
        { duration: '30s', target: 50 },
        { duration: '1m', target: 200 },
        { duration: '30s', target: 0 },
      ],
  thresholds: {
    'goal_submit_duration': ['p(95)<300'],
    'goal_submit_errors': ['rate<0.01'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';

export default function () {
  const payload = JSON.stringify({
    goal: `Load test goal ${Date.now()}`,
    priority: 'low',
    dry_run: true,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY,
    },
  };

  const start = Date.now();
  const res = http.post(`${BASE_URL}/goals`, payload, params);
  const duration = Date.now() - start;

  submitDuration.add(duration);

  const ok = check(res, {
    'status is 200 or 201': (r) => r.status === 200 || r.status === 201,
    'has goal_id': (r) => {
      try { return JSON.parse(r.body).goal_id !== undefined; }
      catch { return false; }
    },
  });

  submitErrors.add(!ok);
  sleep(0.1);
}
