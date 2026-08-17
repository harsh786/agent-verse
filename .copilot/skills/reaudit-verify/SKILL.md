# Re-Audit & Re-Verification Skill — AgentVerse

## Purpose

This skill continuously verifies that implementation matches the specification.
It runs in a LOOP until all spec requirements are implemented.
It never stops until everything is done.

## Spec File Location

```
/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/docs/superpowers/specs/2026-08-17-ai-organization-os-design.md
```

---

## Audit Protocol (Run in This Exact Order)

### PHASE 1 — Inventory the Spec

Read the spec and extract all features organized by phase:

```bash
SPEC="agent-verse-backend/docs/superpowers/specs/2026-08-17-ai-organization-os-design.md"

# Extract all ## headings (feature areas):
grep -n "^## PART\|^## N[0-9]\|^## O[0-9]\|^## P[0-9]\|^## Q\|^## R\|^## S\|^## T\|^## U\|^## V\|^## W\|^## X\|^## Y\|^## Z\|^## AA" "$SPEC"

# Count total lines:
wc -l "$SPEC"
```

### PHASE 2 — Check Backend Implementation

For each backend feature in the spec, verify:

```bash
BACKEND="agent-verse-backend/app"

echo "=== API ENDPOINTS IMPLEMENTED ==="
grep -r "@router\." $BACKEND --include="*.py" -h | grep -oE '"(/v1/[^"]+)"' | sort -u

echo "=== DB MODELS EXIST ==="
grep -r "class.*Base" $BACKEND --include="*.py" -l | sort

echo "=== MIGRATIONS EXIST ==="
ls agent-verse-backend/app/db/migrations/versions/ | wc -l

echo "=== CELERY TASKS DEFINED ==="
grep -r "@celery_app.task" $BACKEND --include="*.py" | grep "name=" | sed 's/.*name="\([^"]*\)".*/\1/' | sort

echo "=== OTEL SPANS COVERED ==="
grep -r "start_as_current_span" $BACKEND --include="*.py" | wc -l

echo "=== CIRCUIT BREAKERS ==="
grep -r "CircuitBreaker" $BACKEND --include="*.py" | wc -l

echo "=== RLS POLICIES ==="
grep -r "ENABLE ROW LEVEL SECURITY\|CREATE POLICY" \
  agent-verse-backend/app/db/migrations/versions/ | wc -l
```

### PHASE 3 — Check Frontend Implementation

```bash
FRONTEND="agent-verse-frontend/src"

echo "=== FEATURE PAGES EXIST ==="
find $FRONTEND/features -name "*Page.tsx" | sort

echo "=== LAZY ROUTES (code splitting) ==="
grep -r "React.lazy\|lazy(() =>" $FRONTEND --include="*.tsx" | wc -l

echo "=== ERROR BOUNDARIES ==="
grep -r "ErrorBoundary" $FRONTEND --include="*.tsx" | wc -l

echo "=== TANSTACK QUERY HOOKS ==="
grep -r "useQuery\|useMutation" $FRONTEND --include="*.ts" --include="*.tsx" | wc -l

echo "=== SSE CONNECTIONS ==="
grep -r "EventSource\|useEventStream\|useGoalStream" $FRONTEND --include="*.ts" --include="*.tsx" | wc -l

echo "=== ACCESSIBILITY (aria-label etc) ==="
grep -r "aria-label\|aria-live\|role=" $FRONTEND --include="*.tsx" | wc -l
```

### PHASE 4 — Check Test Coverage

```bash
cd agent-verse-backend
echo "=== BACKEND TEST FILES ==="
find tests -name "test_*.py" | wc -l

echo "=== BACKEND COVERAGE ==="
uv run pytest --cov=app --cov-report=term-missing -q 2>&1 | tail -5

cd ../agent-verse-frontend
echo "=== FRONTEND TEST FILES ==="
find src -name "*.test.tsx" -o -name "*.test.ts" | wc -l

echo "=== FRONTEND TESTS PASS ==="
npx vitest run --reporter=verbose 2>&1 | tail -5
```

### PHASE 5 — Compare Against Spec Phases

Check each phase from the implementation roadmap:

```
PHASE 0 (Foundation):
  □ Docker multi-stage Dockerfile exists
  □ CI/CD GitHub Actions workflows exist (11 found)
  □ Alembic migrations exist (103+ found)
  □ FastAPI app with health checks
  □ Celery worker configured
  □ OpenTelemetry setup

PHASE 1 (Core Org + Mission):
  □ Org CRUD endpoints (/v1/org/*)
  □ Mission lifecycle endpoints (/v1/missions/*)
  □ LangGraph agent loop (app/agent/loop.py)
  □ HITL approval gateway (app/governance/hitl.py)
  □ SSE streaming (mission progress)
  □ JARVIS dark layout shell (src/features/)

PHASE 2 (Multi-tenancy):
  □ RLS on all tables (app/db/migrations)
  □ TenantMiddleware (app/tenancy/middleware.py)
  □ RBAC (app/db/migrations/0022_rbac_tables.py)
  □ SSO/SAML (app/auth/)
  □ Billing tiers (app/tenancy/)

PHASE 3 (Intelligence):
  □ LongTermMemoryStore (app/memory/)
  □ RAG system (app/rag/)
  □ Semantic cache (app/rag/)
  □ Eval suite (app/evals/)
  □ Content safety (app/guardrails_v2/)

PHASE 4 (Knowledge/Graphify):
  □ Graphify pipeline (app/knowledge_graph/)
  □ Community detection
  □ Knowledge graph UI (src/features/knowledge-graph/)
  □ Obsidian vault sync (app/ingestion/)

PHASE 5 (Voice + Collab):
  □ STT/TTS (app/voice/)
  □ WebSocket collaboration (app/collab/)
  □ Real-time cursor presence
  □ Comments/discussion

PHASE 6 (Gateway):
  □ UCG (app/net/ or app/integrations/)
  □ Telegram/Slack bots
  □ MCP connector marketplace
  □ A2A protocol (app/coordination/)

PHASE 7 (Enterprise):
  □ Compliance reports (app/enterprise/)
  □ GDPR self-service
  □ Custom fine-tuning (app/intelligence/)
  □ Red team testing (app/enterprise/)
```

---

## Gap Reporting Format

When a gap is found, report in this format:

```
GAP DETECTED:
  Spec section: PART 4 — Complete Enterprise Organization
  Feature: Department hierarchy API
  Status: NOT IMPLEMENTED
  Evidence: No /v1/org/*/departments endpoint in app/*/router.py
  Action needed: Implement app/departments/ module with CRUD
  Priority: PHASE 1 (MVP)
  
  Files to create:
    app/departments/router.py
    app/departments/service.py
    app/departments/repository.py
    app/departments/schemas.py
    app/departments/models.py
    app/db/migrations/XXXX_departments.py
    tests/departments/test_service.py
    tests/departments/test_router.py
```

---

## LOOP PROTOCOL (Run Until Done)

```
ITERATION 1:
  1. Run full audit (phases 1–5 above)
  2. List all gaps found
  3. Prioritize by phase (Phase 0 first, Phase 7 last)
  4. Implement highest-priority gap
  5. Verify implementation: tests pass + coverage ≥ 90%
  6. Mark gap as DONE
  7. → Go to ITERATION 2

REPEAT until: ALL gaps closed, ALL tests pass, ALL coverage ≥ 90%

STOPPING CONDITION:
  All spec features implemented AND
  uv run pytest --cov-fail-under=90 passes AND
  npx vitest run --coverage passes with ≥90% AND
  All security tests pass
```

---

## Verification Commands (Run After Claiming "Done")

```bash
# Backend — must all pass before claiming done:
cd agent-verse-backend
uv run ruff check app/                          # 0 errors
uv run mypy app/                                # 0 errors
uv run pytest --cov=app --cov-fail-under=90    # ≥90% coverage
uv run pytest -m integration                   # integration tests pass

# Frontend — must all pass:
cd agent-verse-frontend
npm run typecheck                               # 0 errors
npm run lint                                   # 0 warnings
npx vitest run --coverage                      # ≥90% coverage
npx playwright test                            # E2E tests pass

# Security — must all pass:
cd agent-verse-backend
uv run pytest tests/security/ -v              # all security tests pass
uv run pip-audit --strict                     # 0 CVEs

cd agent-verse-frontend
npm audit --audit-level=high                  # 0 high/critical CVEs
```
