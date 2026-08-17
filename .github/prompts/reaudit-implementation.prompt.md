---
description: "Re-audit and re-verify all implementation against the spec. Finds gaps, reports them, loops until everything is implemented."
---

# Re-Audit: Verify Implementation Against Spec

Run a complete audit of what's been implemented vs what the spec requires.
Report ALL gaps. Loop until everything is done.

## Current Spec File
```
agent-verse-backend/docs/superpowers/specs/2026-08-17-ai-organization-os-design.md
```

## AUDIT SCOPE

### 1. Backend Completeness Check

Run these commands and report what's MISSING:

```bash
# All API endpoints:
grep -r "prefix=" agent-verse-backend/app --include="*.py" | \
  grep "APIRouter" | grep -oE '"/v1/[^"]*"' | sort -u

# All domain modules:
ls agent-verse-backend/app/

# All DB migrations:
ls agent-verse-backend/app/db/migrations/versions/ | wc -l

# Required patterns per spec:
grep -r "start_as_current_span" agent-verse-backend/app --include="*.py" | wc -l
grep -r "CircuitBreaker" agent-verse-backend/app --include="*.py" | wc -l
grep -r "OutboxEvent" agent-verse-backend/app --include="*.py" | wc -l
```

### 2. Frontend Completeness Check

```bash
# All feature pages:
find agent-verse-frontend/src/features -name "*Page.tsx" | sort

# TanStack Query hooks:
find agent-verse-frontend/src -name "use*.ts" -path "*/hooks/*" | wc -l

# ErrorBoundary usage:
grep -r "ErrorBoundary" agent-verse-frontend/src --include="*.tsx" | wc -l

# Lazy loading:
grep -r "React.lazy\|lazy(() =>" agent-verse-frontend/src --include="*.tsx" | wc -l
```

### 3. Test Coverage Check

```bash
cd agent-verse-backend
uv run pytest --cov=app --cov-report=term-missing -q 2>&1 | tail -10

cd agent-verse-frontend
npx vitest run --reporter=verbose 2>&1 | tail -10
```

### 4. Security Check

```bash
cd agent-verse-backend
uv run pytest tests/ -k "security or access_control" -v 2>&1 | tail -20
```

## GAP ANALYSIS FORMAT

For each gap found:

```
GAP #N: <Feature Name>
  Spec: <Part/Supplement reference>
  Status: NOT_STARTED | PARTIAL | WRONG_PATTERN
  Evidence: <grep result showing absence>
  Fix: <exactly what to do>
  Files: <list of files to create/modify>
  Tests: <tests to write first (TDD)>
  Priority: P0–P7
```

## IMPLEMENTATION LOOP

After reporting all gaps:
1. Implement Gap P0 first (highest priority)
2. Write tests FIRST (RED phase)
3. Implement (GREEN phase)
4. Refactor
5. Verify: `uv run pytest tests/<module>/ --cov-fail-under=90`
6. Mark gap DONE
7. **Start over from AUDIT SCOPE** — find next gap
8. **Repeat until ZERO GAPS REMAIN**

## STOPPING CONDITION

Only stop when ALL of these are true:
```bash
# These must ALL exit 0:
cd agent-verse-backend && uv run pytest --cov-fail-under=90 -q
cd agent-verse-frontend && npx vitest run
cd agent-verse-frontend && npm run typecheck
cd agent-verse-backend && uv run ruff check app/
cd agent-verse-backend && uv run mypy app/
```
