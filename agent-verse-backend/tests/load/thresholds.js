/**
 * AgentVerse SLA thresholds — reference for CI load test gates.
 * Import these into other scripts for consistency.
 */
export const SLA_THRESHOLDS = {
  // Availability
  http_req_failed: ['rate<0.01'],          // 99% availability

  // Latency (AgentVerse targets)
  'http_req_duration{endpoint:health}': ['p(99)<50'],     // Health: 50ms p99
  'http_req_duration{endpoint:auth}': ['p(99)<100'],      // Auth: 100ms p99
  'http_req_duration{endpoint:list}': ['p(95)<500'],      // List APIs: 500ms p95
  'http_req_duration{endpoint:submit}': ['p(95)<2000'],   // Goal submit: 2s p95

  // Throughput
  'goal_submit_latency': ['p(95)<2000'],
  'auth_latency': ['p(99)<100'],
};

export const ENVIRONMENT_DEFAULTS = {
  development: { vus: 5, duration: '1m' },
  staging: { vus: 25, duration: '5m' },
  production: { vus: 100, duration: '10m' },
};
