# Graphify Code Context Skill — AgentVerse

## When to Invoke
Use this skill when:
- Navigating a large area of the codebase for the first time
- Understanding dependencies between modules before modifying them
- Doing a refactor that spans multiple files
- Reviewing how a feature is connected across layers

## Global Skill Reference
This skill uses: `~/.copilot/skills/graphify/SKILL.md`

---

## Graphify the Codebase

Graphify builds a knowledge graph from ANY input. For code context:

### Step 1 — Build a Graph of the Target Area

```
Input: The relevant source files
Goal: Understand call graphs, dependencies, data flow

Example: Before modifying app/missions/
  Feed graphify:
  - app/missions/router.py
  - app/missions/service.py
  - app/missions/repository.py
  - app/missions/models.py
  - tests/missions/
  - src/features/goals/ (frontend counterpart)
```

### Step 2 — Extract Key Relationships

Graphify will identify:

```
NODES (entities):
  - Functions/methods
  - Classes
  - DB tables
  - API endpoints
  - Frontend components/hooks

EDGES (relationships):
  - router calls service
  - service calls repository
  - repository queries Model
  - endpoint returns Schema
  - frontend hook calls API endpoint
  - test covers function X
```

### Step 3 — Identify Impact Zones

Before modifying any function, ask graphify:

```
"What calls create_mission()?"
"What does MissionService depend on?"
"If I change MissionRepository.list_cursor(), what breaks?"
"Which tests cover this code path?"
```

---

## Code Context Patterns

### Understanding a Module (Read These Files in Order)

```bash
# For app/<domain>/:
1. models.py         → data shape (what's in DB)
2. schemas.py        → API contract (what clients send/receive)
3. repository.py     → query patterns (how data is accessed)
4. service.py        → business logic (what the domain does)
5. router.py         → HTTP endpoints (how it's exposed)
6. tests/            → behaviour specification (what it should do)
```

### Tracing a Request End-to-End

```
HTTP Request → router.py
  → depends_on(TenantContext) → tenancy/middleware.py
  → calls service.create()
    → calls repository.create()
      → queries ORM models
        → triggers RLS policy
    → emits OutboxEvent
      → outbox worker picks up
        → publishes to Redis Streams
          → SSE endpoint delivers to client
```

### Finding All Usages of a Pattern

```bash
# Find all places that use circuit breaker (before adding a new one):
grep -r "CircuitBreaker\|circuit_breaker" agent-verse-backend/app/ --include="*.py"

# Find all endpoints missing rate limit:
grep -r "@router\." agent-verse-backend/app/ --include="*.py" -l | \
  xargs grep -L "rate_limit\|RateLimiter"

# Find all components missing ErrorBoundary:
grep -r "lazy(() =>" agent-verse-frontend/src/ --include="*.tsx" -l | \
  xargs grep -L "ErrorBoundary"

# Find all service methods missing OTel span:
grep -r "async def " agent-verse-backend/app/*/service.py | \
  grep -v "start_as_current_span"
```

---

## Pre-Modification Checklist (Use Graphify)

Before modifying `app/<domain>/`:

```
□ Read models.py — understand the data shape
□ Read all 44 migration files for this domain — understand schema history
□ Run: grep -r "from app.<domain>" app/ — find all importers
□ Run: grep -r "/<domain>" app/*/router.py — find related endpoints
□ Read tests/ — understand expected behaviour before changing it
□ Check FOREIGN KEY dependencies — changing model.id affects all referencing tables
□ Check RLS policy — changing column names may break the policy
```

## AgentVerse Module Dependency Map

```
tenancy/  ←──────── (all domains depend on TenantContext)
core/     ←──────── (all domains depend on Settings, errors, types)
reliability/ ←────── (all domains use circuit breaker, bulkhead)
providers/   ←────── (agent, intelligence, graphify use LLM provider)
    ↓
agent/    ←── orchestration, services
    ↓
missions/ ←── api, coordination
    ↓
knowledge/ ←── graphify, rag
    ↓
governance/ ←── audit, cost, policy
    ↓
scaling/ ←── all async operations
```
