# Brainstorm → Spec → Plan Skill — AgentVerse

## When to Invoke
**MANDATORY** before implementing ANY new feature, component, or system change.
No code should be written before this skill produces a plan.

## Global Skill References
This skill chains three global skills:
1. `~/.agents/skills/brainstorming/SKILL.md` — explore intent and requirements
2. `~/.agents/skills/writing-plans/SKILL.md` — write the implementation plan
3. `~/.agents/skills/test-driven-development/SKILL.md` — write tests first

---

## Phase 1 — Brainstorm (Explore Before Building)

### Questions to Answer Before Writing ANY Code

```markdown
FEATURE INTENT:
□ What problem does this solve for the user?
□ Who are the users (tenant admin? agent? viewer?)?
□ What is the simplest version that delivers value?

SCOPE BOUNDARIES:
□ What is IN scope for this iteration?
□ What is explicitly OUT of scope?
□ What existing code does this touch?

TECHNICAL APPROACH:
□ Which existing modules in app/ can be reused?
□ Which existing components in src/features/ can be reused?
□ Does this need a new DB table or extend existing?
□ Does this need a new API endpoint?
□ Does this affect existing API contracts (breaking change)?

RISKS:
□ Security implications? (auth, data access, injection)
□ Performance implications? (N+1, missing index, slow path)
□ Multi-tenant implications? (tenant isolation, RLS)
□ Breaking change for existing clients?
```

### Output of Brainstorm Phase

```markdown
## Feature: <name>

**Problem**: <one sentence>
**Users**: <who uses it>
**Value**: <measurable outcome>

**In scope**: <bullet list>
**Out of scope**: <bullet list>

**Existing code to reuse**:
- `app/missions/service.py` — extend `create_mission()`
- `src/features/missions/hooks/useMissions.ts` — add new mutation

**New code required**:
- `app/missions/schemas.py` — add `deadline_at` field
- `app/db/migrations/0105_add_deadline.py` — new column

**Risks**:
- Breaking change: deadline_at is new optional field → non-breaking
- Performance: deadline queries need index → included in migration
```

---

## Phase 2 — Specification

```markdown
## Specification: Mission Deadline Feature

### API Contract
POST /v1/missions
Request:  { title, priority, deadline_at?: ISO8601 datetime | null }
Response: { id, title, priority, deadline_at, status, created_at }

GET /v1/missions
Response: { data: [Mission], cursor, hasMore }
          # Mission.deadline_at: string | null
          # New filter: ?overdue=true (missions past deadline)

### Data Model
org_missions table:
  + deadline_at: TIMESTAMPTZ NULL (nullable — backward compatible)
  + INDEX: idx_missions_deadline ON (tenant_id, deadline_at) WHERE deadline_at IS NOT NULL

### Business Rules
- deadline_at can be null (no deadline)
- deadline_at must be in the future on creation
- Missions past deadline transition to 'overdue' status (Celery Beat job)
- 'overdue' missions emit mission.deadline_exceeded event

### Error Cases
- deadline_at in the past → 422 "deadline must be in the future"
- deadline_at invalid format → 422 standard validation error

### Frontend Changes
- MissionCard shows deadline badge if set (red if < 24h)
- MissionForm adds optional deadline picker
- MissionsPage adds "Overdue" filter tab
```

---

## Phase 3 — Implementation Plan

```markdown
## Plan: Mission Deadline Feature

### Order (strictly sequential — each depends on previous)

Step 1: DB migration (prerequisite for everything)
  - File: app/db/migrations/0105_add_missions_deadline.py
  - Add: deadline_at TIMESTAMPTZ NULL to org_missions
  - Add: index CONCURRENTLY

Step 2: Backend schemas + service (test first)
  - Write tests: tests/missions/test_mission_service.py
    - test_create_with_deadline_stores_it
    - test_create_with_past_deadline_raises
    - test_list_overdue_missions
  - Then implement: app/missions/schemas.py, service.py

Step 3: Backend router (test first)
  - Write tests: tests/missions/test_mission_router.py
    - test_post_mission_with_deadline_returns_201
    - test_post_mission_past_deadline_returns_422
  - Then implement: app/missions/router.py

Step 4: Celery task for overdue detection
  - Write tests: tests/missions/test_deadline_task.py
  - Implement: app/missions/tasks.py::check_overdue_missions
  - Register in CELERY_BEAT_SCHEDULE (every 5 min)

Step 5: Frontend hooks (test first)
  - Write tests: hooks/__tests__/useMissions.test.ts
    - test mutation includes deadline_at
    - test overdue filter
  - Implement: hooks/useMissions.ts

Step 6: Frontend components (test first)
  - Write tests: __tests__/DeadlineBadge.test.tsx
  - Implement: components/DeadlineBadge.tsx, MissionForm.tsx update

Step 7: E2E test
  - Write: e2e/missions-deadline.spec.ts
  - Verify full flow: create with deadline → overdue detected → badge shown

### Verification Before PR
  □ uv run pytest tests/missions/ --cov-fail-under=90
  □ npm run test src/features/missions --coverage
  □ npm run typecheck
  □ Visual regression: DeadlineBadge snapshot
  □ a11y: deadline picker is keyboard accessible
```

---

## Phase 4 — TDD Execution

Always execute plans using the TDD skill:
`→ Read .copilot/skills/tdd/SKILL.md`

```
For each Step in the plan:
  1. Write the test (RED — it must fail first)
  2. Run test: confirm it fails
  3. Write minimum implementation (GREEN — make it pass)
  4. Run test: confirm it passes
  5. Refactor (same tests must still pass)
  6. Move to next step
```
