/**
 * Goal submission throughput test.
 * Tests the most expensive path: goal submit + status poll.
 * Run: k6 run tests/load/goal_submission.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';
const VUS = parseInt(__ENV.VUS || '10');
const DURATION = __ENV.DURATION || '5m';

const goalsSubmitted = new Counter('goals_submitted');
const goalsFailed = new Counter('goals_failed');
const submitLatency = new Trend('goal_submit_latency', true);
const pollLatency = new Trend('goal_poll_latency', true);
const errorRate = new Rate('error_rate');

export const options = {
  stages: [
    { duration: '30s', target: VUS },      // Ramp up
    { duration: DURATION, target: VUS },    // Steady state
    { duration: '30s', target: 0 },         // Ramp down
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],           // <5% HTTP failures
    goal_submit_latency: ['p(95)<2000'],      // Submit p95 < 2s
    goal_poll_latency: ['p(95)<200'],         // Poll p95 < 200ms
    error_rate: ['rate<0.10'],               // <10% application errors
  },
};

const GOALS = [
  'List all open GitHub issues',
  'Summarise the latest 5 Jira tickets',
  'Check if the CI pipeline is passing',
  'List all active agents',
  'Get the current cost metrics',
];

export function setup() {
  const signup = http.post(`${BASE_URL}/tenants/signup`, JSON.stringify({
    name: `load-test-${Date.now()}`,
    email: `load-${Date.now()}@test.com`,
  }), { headers: { 'Content-Type': 'application/json' } });

  if (signup.status === 200 || signup.status === 201) {
    const body = JSON.parse(signup.body);
    return { apiKey: body.api_key || API_KEY };
  }
  return { apiKey: API_KEY };
}

export default function (data) {
  const key = data.apiKey || API_KEY;
  const headers = {
    'X-API-Key': key,
    'Content-Type': 'application/json',
  };
  const goal = GOALS[Math.floor(Math.random() * GOALS.length)];

  // Submit goal (dry_run=true to avoid real LLM calls in load test)
  const submitStart = Date.now();
  const submit = http.post(`${BASE_URL}/goals`, JSON.stringify({
    goal: goal,
    dry_run: true,
    priority: 'normal',
  }), { headers });
  submitLatency.add(Date.now() - submitStart);

  if (!check(submit, {
    'goal submit 2xx': (r) => r.status >= 200 && r.status < 300,
  })) {
    goalsFailed.add(1);
    errorRate.add(1);
    sleep(1);
    return;
  }

  goalsSubmitted.add(1);

  let goalId = null;
  try {
    goalId = JSON.parse(submit.body).id || JSON.parse(submit.body).goal_id;
  } catch (e) {
    errorRate.add(1);
    sleep(1);
    return;
  }

  // Poll goal status (up to 3 times)
  for (let i = 0; i < 3; i++) {
    sleep(0.5);
    const pollStart = Date.now();
    const poll = http.get(`${BASE_URL}/goals/${goalId}`, { headers });
    pollLatency.add(Date.now() - pollStart);

    check(poll, {
      'goal poll 200': (r) => r.status === 200,
    }) || errorRate.add(1);

    try {
      const status = JSON.parse(poll.body).status;
      if (['complete', 'completed', 'failed', 'error'].includes(status)) break;
    } catch {}
  }

  sleep(Math.random() * 2 + 1); // 1-3s think time
}
