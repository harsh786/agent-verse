# AgentVerse Load Tests

Load-test suite for the AgentVerse API.

## Requirements
- k6: `brew install k6` or https://k6.io/docs/getting-started/installation/
- locust: `pip install locust`

## Running k6

Goal submission (p95 < 300 ms):
```bash
k6 run infra/loadtest/goal_submission.js \
  -e BASE_URL=http://localhost:8000 \
  -e API_KEY=av_free_xxx
```

SSE stream (p95 < 500 ms):
```bash
k6 run infra/loadtest/sse_stream.js \
  -e BASE_URL=http://localhost:8000 \
  -e API_KEY=av_free_xxx
```

## CI smoke profile
```bash
k6 run --vus 10 --duration 30s \
  -e BASE_URL=http://localhost:8000 \
  -e API_KEY=av_free_xxx \
  -e SMOKE=1 \
  infra/loadtest/goal_submission.js
```

## Running locust

```bash
locust -f infra/loadtest/locustfile.py \
  --headless -u 50 -r 10 -t 1m \
  --host http://localhost:8000
```

## Autoscale signal

`record_queue_depths` emits `agentverse_desired_workers{plan}` gauges (one per
Celery plan queue: free / starter / professional / enterprise).  These are
consumed by a KEDA `ScaledObject` or Kubernetes HPA via the Prometheus adapter
to automatically scale worker replicas based on queue depth.

Set `TASKS_PER_WORKER` env var (default: 4) to tune the desired-replica formula:

```
desired = max(1, ceil(queue_depth / TASKS_PER_WORKER))
```
