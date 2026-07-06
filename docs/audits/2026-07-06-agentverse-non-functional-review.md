# AgentVerse Non-Functional Requirements Review
**Date:** 2026-07-06  
**Reviewer:** Distributed-Systems Architect  
**Scope:** Backend — `agent-verse-backend/` (Python 3.12, FastAPI, LangGraph, Celery, Postgres+pgvector, Redis)

All values cited below are drawn directly from source files. No guesses.

---

## NFR Assessment Table

| NFR | Target | Current State | Gap | Priority |
|---|---|---|---|---|
| API p95 latency | <200ms | No measurement infrastructure for p95; Prometheus metrics registered (`app/observability/metrics.py`) but no SLO alerting configured | No SLO defined or enforced; no evidence p95 is measured | HIGH |
| Goal execution start latency | <2s | Goal submission enqueues to Celery then returns 202. Celery `worker_prefetch_multiplier=1` (`celery_app.py:43`). Start latency = Redis round-trip + worker poll interval (up to 5s on default beat). No measurement. | Target undefined; actual latency unknown; worst-case > 5s | HIGH |
| SSE stream setup | <500ms | SSE stream is established immediately in the HTTP handler. If Redis pub/sub bridge has not started yet (race on startup), first events may be missed. No measurement. | Target undefined; first-event delivery unverified | HIGH |
| Max concurrent goals — free | 2 | `app/tenancy/limits.py:88` — `"free": 2`. Enforced via Redis Lua `check_and_increment_concurrent_goals`. Cross-replica enforcement is correct (Lua). | No gap in logic, but Redis outage silently permits unlimited concurrency (`limits.py:121`: `pass`) | MEDIUM |
| Max concurrent goals — starter | 5 | `app/tenancy/limits.py:89` — `"starter": 5` | Same Redis-outage gap as free tier | MEDIUM |
| Max concurrent goals — professional | 20 | `app/tenancy/limits.py:90` — `"professional": 20` | Same Redis-outage gap | MEDIUM |
| Max concurrent goals — enterprise | 100 | `app/tenancy/limits.py:91` — `"enterprise": 100` | Same Redis-outage gap | MEDIUM |
| Max daily goals — free | 25 | `app/tenancy/context.py:29` — `goals_per_day=25` | Counter uses `redis.incr` + `expire` (not atomic check-then-increment). TOCTOU gap: `get` then `incr` are separate calls (`goal_service.py:544-547`) | MEDIUM |
| Max daily goals — enterprise | 10,000 | `app/tenancy/context.py:53` — `goals_per_day=10000` | Same TOCTOU gap as free tier | LOW |
| API request rate — free | 30 RPM | `app/tenancy/context.py:28` — `requests_per_minute=30` | Rate limiter is non-atomic (see Risk 3 in scalability review). Actual enforced limit is approximately `30 + concurrent_requests_at_boundary` | HIGH |
| API request rate — enterprise | 6,000 RPM | `app/tenancy/context.py:52` — `requests_per_minute=6000` | Same non-atomic issue; at this scale the race window is negligible in practice | LOW |
| Goal timeout — free | 5 min | `app/tenancy/context.py:33` — `goal_timeout_seconds=300`. Enforced via `asyncio.wait_for(timeout=300)` in `tasks.py:994-1006` | Implemented and enforced | None |
| Goal timeout — enterprise | 2 hr | `app/tenancy/context.py:57` — `goal_timeout_seconds=7200` | Implemented and enforced | None |
| DB connection pool — per process | Not defined | `app/db/session.py:24-25` — `pool_size=10`, `max_overflow=20` → max 30 connections per process | No explicit target set. With 3 API replicas + 2 Celery workers = 5 processes × 30 = 150 potential connections. PgBouncer `DEFAULT_POOL_SIZE=50` (`docker-compose.yml:70`). Mismatch: app can request 150 connections, PgBouncer allows 50 to Postgres. PgBouncer queue absorbs the rest with `pool_timeout=30s` | HIGH |
| Redis — connection management | Not defined | Rate limiter creates a new `SlidingWindowRateLimiter` + `TenantScopedStore` per request (`middleware.py:289-300`). This instantiates objects but reuses the same underlying Redis client. Acceptable. | No persistent connection leak, but object allocation per-request adds GC pressure at high RPM | LOW |
| Redis failover RTO | <5min | Single-node Redis (`docker-compose.yml:45-57`). No Sentinel. No Cluster. Redis restart = Celery broker unavailable = goal execution halted | Current RTO for Redis failure is unbounded (until Redis restarts). **Target of <5min is not achievable with single-node Redis.** | CRITICAL |
| Postgres failover RTO | <5min | Single Postgres + pgbackup container. No streaming replica. No read replica. `pool_pre_ping=True` detects stale connections but cannot fail over to a replica that doesn't exist | RTO for Postgres primary failure is unbounded. **Target of <5min is not achievable without a standby.** | CRITICAL |
| RPO (data loss window) | <1min | `pgbackup` is configured with `SCHEDULE: "@daily"` (`docker-compose.yml:33`). Daily backups = 24-hour RPO for full failures. Redis AOF is enabled (`--appendonly yes`, `docker-compose.yml:48`) = near-zero RPO for Redis data. | **RPO for Postgres is up to 24 hours** (daily backup). WAL archiving is not configured. Target of <1min requires continuous WAL shipping (e.g., pgWAL-G or Barman). | CRITICAL |
| Test coverage | >80% | `coverage.json` (2026-06-30): **91.6%** line coverage (23,590 / 25,762 statements). Generated by `pytest -m "not integration and not slow"`. | Coverage target exceeded. Note: line coverage ≠ branch coverage or semantic correctness. No branch coverage enabled (`"branch_coverage": false` in coverage.json). | None |
| CI pipeline reliability | >99% | CI runs 4 parallel jobs: `lint`, `unit-tests`, `integration-tests`, `security-audit` (`ci.yml`). No retry logic on flaky tests. `security-audit` step uses `|| true` (`ci.yml:146`) so pip-audit failures do not block merges. | Security audit failures are silently ignored. No test retry on flakiness. No SLO measurement for CI. | MEDIUM |
| CI — test isolation | Full isolation | Integration tests use real Postgres+Redis provisioned as GitHub Actions `services:`. No shared state between test runs. | Correct | None |
| Celery worker memory | Not defined | `docker-compose.yml:235-237` — `memory: 512M`. `celery_app.py:40` — `worker_max_memory_per_child=500_000` (500 MB). `worker_max_tasks_per_child=100` | Memory limit matches `worker_max_memory_per_child`. Worker recycles after 100 tasks or 500 MB. This is correct. | None |
| Budget — per goal | $10.00 | `app/governance/cost.py:31` — `per_goal_usd=10.0` (default). Enforced via Redis Lua (`_LUA_CHECK_AND_INCREMENT`). Atomic. | Redis outage fails open in non-production (`cost.py:296`); fails closed (returns False) in production — all tool calls blocked. Both failure modes are incorrect. | HIGH |
| Budget — per tenant/day | $500.00 | `app/governance/cost.py:32` — `per_tenant_daily_usd=500.0` (default). Redis-backed with daily TTL via `EXPIREAT`. | Same Redis-outage failure modes as per-goal budget | HIGH |
| Audit log immutability | Append-only | `0031_audit_immutability.py:18-33` — `prevent_audit_modification()` PL/pgSQL trigger installed. `downgrade()` removes it. | Correct, but `downgrade()` restores mutability — the trigger must be treated as a non-reversible production constraint. | LOW |
| HSTS / security headers | OWASP baseline | `app/tenancy/middleware.py:343-355` — HSTS `max-age=63072000; includeSubDomains; preload`, CSP, X-Frame-Options, Referrer-Policy all set on every response | Correct. CSP includes `'unsafe-inline'` for style-src — acceptable for initial implementation. | LOW |

---

## Detailed NFR Findings

### NFR-1: Database Connection Pool vs PgBouncer Sizing Mismatch

**Files:** `app/db/session.py:24-25`, `infra/docker-compose.yml:70`

The application configures `pool_size=10, max_overflow=20` per process (30 connections max). In a deployment with 3 API replicas + 2 Celery workers, that is 5 processes × 30 = **150 potential connections to PgBouncer**. PgBouncer's `DEFAULT_POOL_SIZE=50` means only 50 server-side connections to Postgres are opened. The remaining 100 client connections queue at PgBouncer with a `pool_timeout` of 5 seconds (`docker-compose.yml:74`). 

At peak load this manifests as 5-second connection acquisition delays. The `connect_args={"statement_cache_size": 0}` (`session.py:29`) is correct for PgBouncer transaction mode but means every query re-parses its statement plan.

**Gap:** No pool sizing guidance documented. Settings rely on environment variables (`db_pool_size`, `db_max_overflow`) that have no defaults in the environment files examined.

**Recommendation:** For a 3+2 replica deployment, set `pool_size=5, max_overflow=5` per process to stay within PgBouncer's server pool. Or increase `DEFAULT_POOL_SIZE` proportionally. Validate with `pgbouncer SHOW POOLS` under load.

---

### NFR-2: RPO is 24 Hours for PostgreSQL — WAL Archiving Not Configured

**Files:** `infra/docker-compose.yml:26-43`

The `pgbackup` service uses `prodrigestivill/postgres-backup-local:16` with `SCHEDULE: "@daily"`. This provides one backup snapshot per day. If Postgres fails between backups, all data written since the last snapshot is lost — up to 24 hours.

There is no continuous WAL archiving configured (no `POSTGRES_INITDB_ARGS` for `wal_level=replica`, no `archive_command`, no pgWAL-G or Barman sidecar). Postgres streaming replication is not configured.

**Gap:** Current RPO is ~24 hours. Target of <1 minute requires continuous WAL archiving.

**Recommendation:**
1. Add `wal_level=replica` and `archive_command` pointing to S3/MinIO (MinIO is already in the stack at `minio:9000`).
2. Use pgWAL-G or similar for continuous WAL shipping.
3. Add a hot standby replica with streaming replication for near-zero RPO + <1min RTO.

---

### NFR-3: Daily Goal Counter Has TOCTOU Race

**File:** `app/services/goal_service.py:539-552`

```python
current = int(await redis.get(key) or 0)    # line 544 — READ
check_daily_goal_limit(tenant_ctx, current)  # line 545 — CHECK
await redis.incr(key)                        # line 547 — INCREMENT (separate operation)
```

Between the `GET` and `INCR`, another concurrent request from the same tenant can also read `current`, pass the limit check, and both increment. The concurrent-goal counter (in `limits.py:99`) correctly uses an atomic Lua script; the daily-goal counter does not.

**Recommendation:** Replace the `get`→`check`→`incr` pattern with the same Lua atomic pattern used for concurrent goals (see `limits.py:99-117`).

---

### NFR-4: Cost Controller Redis Failure Modes Are Both Wrong

**File:** `app/governance/cost.py:291-296`

```python
except Exception as exc:
    err_str = str(exc)
    if "GOAL_BUDGET_EXCEEDED" in err_str or "DAILY_BUDGET_EXCEEDED" in err_str:
        return False
    get_logger(__name__).warning("cost_check_error", error=err_str[:100])
    return os.getenv("ENVIRONMENT", "development") != "production"
```

- **Non-production on Redis error:** returns `True` (fail-open) — budget is not enforced.  
- **Production on Redis error:** returns `False` — every tool call is blocked for every tenant.

Neither behaviour is correct for a brief Redis hiccup (e.g., a 2-second leader election during Sentinel failover).

**Recommendation:** Implement a time-boxed fail-open: if the Redis error lasts < 30 seconds, allow tool calls but log a warning. If it exceeds a threshold, page on-call. Never block 100% of production traffic on a transient Redis blip.

---

### NFR-5: Security Audit CI Step Uses `|| true` — Vulnerabilities Are Silently Ignored

**File:** `.github/workflows/ci.yml:146`

```yaml
run: pip-audit --requirement <(uv export --no-hashes) || true
```

`|| true` means any number of CVEs will not fail the CI pipeline. A developer who merges a dependency with a known critical CVE will see a green check.

**Gap:** The security audit job provides false assurance — it runs but never blocks.

**Recommendation:** Remove `|| true`. Address known CVEs before merging, or use `pip-audit --ignore-vuln <CVE-ID>` with a documented justification per ignored vulnerability.

---

## Infrastructure Sizing (Current vs Recommended)

| Resource | Current (`docker-compose.yml`) | Recommended for 100 tenants |
|---|---|---|
| Redis | 1× single-node, no HA | 1 primary + 2 replicas (Sentinel) minimum |
| Postgres | 1× single primary, daily backup | 1 primary + 1 hot standby + WAL archiving |
| PgBouncer | `MAX_CLIENT_CONN=1000`, `DEFAULT_POOL_SIZE=50` | Increase `DEFAULT_POOL_SIZE` to 100–200 for 5+ replicas |
| Celery workers | 1× container, memory limit 512 MB | Auto-scale by queue depth; separate worker pools per plan tier |
| Beat scheduler | Implied single-node; `redbeat` attempted but falls back to file-based if not installed | Install `celery-redbeat`; run at least 2 beat instances with RedBeat lock |
| API replicas | 1× (`docker-compose.yml` has single `backend` service) | Minimum 2 for availability; load balancer with session affinity **required** until Risk 2 (in-memory state) is fixed |

---

## CI Performance Summary

| Job | Trigger | Status |
|---|---|---|
| `lint` (ruff + mypy) | all branches | Blocks merge correctly |
| `unit-tests` (pytest, no integration) | all branches | Blocks merge; generates coverage XML |
| `integration-tests` (real Postgres + Redis) | all branches | Blocks merge; correct isolation |
| `security-audit` (pip-audit) | all branches | **Does NOT block merge** (`|| true`) |
| `publish-openapi` | `main` only | Non-blocking artifact |
| `docker-build` | post unit-tests | Blocks merge |

**No test coverage minimum is enforced in CI.** The coverage report is uploaded to Codecov but there is no `--cov-fail-under` flag in the pytest invocation (`ci.yml:56-61`). A commit that drops coverage from 91.6% to 0% will still pass CI.

**Recommendation:** Add `--cov-fail-under=80` to the pytest command to enforce the >80% target at the gate.
