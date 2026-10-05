# AgentVerse real-world scenario report

Generated 2026-10-05T15:14:30+0530 against the live local stack.

Tests: passed=2
Scenarios: passed=2

## Scenarios

| Scenario | Result | Tests (pass/fail/skip) | Skip reason |
|---|---|---|---|
| SCHED-PLAN-FLOOR | **passed** | 1/0/0 |  |
| WF-SCHEDULE-PLAN-FLOOR | **passed** | 1/0/0 |  |

## Metrics

| Scenario | Test | Metrics |
|---|---|---|

## Tests

| Scenario | Suite | Result | Duration (s) | Key evidence | Failure detail |
|---|---|---|---|---|---|
| SCHED-PLAN-FLOOR | backend | **passed** | 0.1 | {"plan": "free", "floor_s": 900, "at_floor_http": 201, "below_floor_http": 422, "every_minute_cron_http": 422} |  |
| WF-SCHEDULE-PLAN-FLOOR | backend | **passed** | 0.0 | {"plan": "free", "plan_floor_s": 900, "create_http": 422, "create_detail": "{\"detail\":\"schedule '* * * * *': The free plan allows a schedule to run at most every 15 min; this one would run every 1 min\"}"} |  |

## Failure details

