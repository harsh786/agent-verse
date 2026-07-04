/**
 * k6 load test — SSE event latency
 * Target: p95 < 500 ms
 *
 * Usage:
 *   k6 run infra/loadtest/sse_stream.js \
 *     -e BASE_URL=http://localhost:8000 \
 *     -e API_KEY=av_free_xxx
 *
 * CI smoke (low VUs):
 *   k6 run -e SMOKE=1 -e BASE_URL=... -e API_KEY=... infra/loadtest/sse_stream.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';

const SMOKE = __ENV.SMOKE === '1';
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';

export const options = {
  vus: SMOKE ? 10 : 100,
  duration: SMOKE ? '30s' : '1m',
  thresholds: {
    'http_req_duration{name:sse_connect}': ['p(95)<500'],
  },
};

export default function () {
  // Submit a goal first, then immediately connect SSE
  const goalRes = http.post(
    `${BASE_URL}/goals`,
    JSON.stringify({ goal: 'SSE test goal', priority: 'low', dry_run: true }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
      },
    }
  );

  if (goalRes.status !== 200 && goalRes.status !== 201) {
    return;
  }

  let goal_id;
  try {
    goal_id = JSON.parse(goalRes.body).goal_id;
  } catch (_) {
    return;
  }
  if (!goal_id) return;

  // Connect to SSE stream — measure time-to-first-byte
  const res = http.get(
    `${BASE_URL}/goals/${goal_id}/stream`,
    {
      headers: {
        'Accept': 'text/event-stream',
        'X-API-Key': API_KEY,
      },
      tags: { name: 'sse_connect' },
    }
  );

  check(res, { 'sse_ok': (r) => r.status === 200 });
  sleep(0.2);
}
