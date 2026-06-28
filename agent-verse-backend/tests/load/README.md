# AgentVerse Load Tests

Uses [k6](https://k6.io/) for load/performance testing.

## Prerequisites
```bash
brew install k6  # macOS
```

## Run

```bash
# Smoke test (2 VUs, 1 min)
k6 run tests/load/smoke.js

# Goal submission load test (50 VUs, 5 min)
k6 run tests/load/goal_submission.js

# API key auth load test
k6 run tests/load/auth_throughput.js

# Full soak test (10 VUs, 30 min)
k6 run --env DURATION=30m tests/load/soak.js
```

## Environment Variables
- `BASE_URL` - API base URL (default: http://localhost:8000)
- `API_KEY` - Valid API key for authenticated tests
- `DURATION` - Test duration (default per-script)
- `VUS` - Virtual users (default per-script)
