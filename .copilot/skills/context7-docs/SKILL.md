# Context7 Docs Skill — AgentVerse

## When to Invoke
Use BEFORE writing code with any library, framework, or tool.
Even for well-known libraries — APIs change, your training data may be outdated.

## Global Skill Reference
This skill uses: `~/.agents/skills/find-docs/SKILL.md`
Follow the steps in that file, then apply AgentVerse context below.

---

## Priority Libraries to Always Verify via Context7

### Backend

```bash
# Run BEFORE using any of these:
npx ctx7@latest library fastapi "dependency injection async"
npx ctx7@latest library sqlalchemy "async session select options"
npx ctx7@latest library langgraph "state graph conditional edges"
npx ctx7@latest library pydantic "model_validator v2 config"
npx ctx7@latest library alembic "create index concurrently"
npx ctx7@latest library celery "task retry exponential backoff"
npx ctx7@latest library opentelemetry-python "start_as_current_span"
npx ctx7@latest library pgvector "hnsw index cosine distance"
npx ctx7@latest library structlog "async context vars"
```

### Frontend

```bash
npx ctx7@latest library "react" "hooks useTransition concurrent"
npx ctx7@latest library "tanstack-query" "useInfiniteQuery optimistic"
npx ctx7@latest library "zustand" "subscribeWithSelector persist"
npx ctx7@latest library "framer-motion" "useReducedMotion AnimatePresence"
npx ctx7@latest library "xyflow" "handle edge custom node"
npx ctx7@latest library "vitest" "msw mock coverage thresholds"
npx ctx7@latest library "playwright" "screenshot visual regression"
npx ctx7@latest library "d3-force" "forceSimulation forceLink"
```

---

## Quick Reference (Frequently Used)

### LangGraph — Latest State Graph API

```python
# Always verify with: npx ctx7@latest docs /langchain-ai/langgraph
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

# Correct LangGraph 0.2+ pattern:
graph = StateGraph(AgentState)
graph.add_node("planner",  planner_node)
graph.add_node("executor", executor_node)
graph.add_node("verifier", verifier_node)

graph.set_entry_point("planner")
graph.add_edge("planner", "executor")
graph.add_conditional_edges("verifier", route_after_verify, {
    "complete": END,
    "replan":   "planner",
    "fail":     END,
})
compiled = graph.compile(checkpointer=AsyncRedisSaver(redis))
```

### SQLAlchemy 2 Async — Latest Patterns

```python
# Always verify with: npx ctx7@latest docs /sqlalchemy/sqlalchemy
# SQLAlchemy 2.0 async — key API changes from 1.4:

# session.execute() returns CursorResult (not Query)
result = await session.execute(select(Mission).where(...))
missions = result.scalars().all()   # or .scalar_one()

# Eager loading — ALWAYS use options():
await session.execute(
    select(Mission)
    .options(selectinload(Mission.agent))     # for 1:M
    .options(joinedload(Mission.org))         # for M:1
)

# Bulk insert (2.0 style):
await session.execute(
    insert(Mission).values([
        {"tenant_id": t, "title": "M1"},
        {"tenant_id": t, "title": "M2"},
    ])
)
```

### TanStack Query 5 — Latest API

```typescript
// Always verify: npx ctx7@latest docs /tanstack/query
// v5 breaking changes from v4:

// useQuery options changed:
const { data } = useQuery({
  queryKey:  ['missions', orgId],
  queryFn:   () => api.missions.list(orgId),
  gcTime:    5 * 60_000,   // was: cacheTime in v4
  staleTime: 30_000,
});

// useMutation options:
const mutation = useMutation({
  mutationFn: api.missions.create,
  // v5: no onSuccess/onError in useMutation by default
  // Use queryClient.setMutationDefaults() for global defaults
});
```

### Pydantic v2 — Latest Validators

```python
# Always verify: npx ctx7@latest docs /pydantic/pydantic
# v2 breaking changes from v1:

# model_config replaces class Config:
class MyModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,   # was orm_mode=True in v1
        extra="forbid",
        str_strip_whitespace=True,
    )

# @validator → @field_validator (v2):
@field_validator("deadline_at")
@classmethod
def deadline_must_be_future(cls, v: datetime | None) -> datetime | None:
    if v and v < datetime.utcnow():
        raise ValueError("deadline must be in the future")
    return v

# Cross-field: @model_validator (v2):
@model_validator(mode="after")
def check_fields(self) -> "MyModel":
    # self is the model instance (mode="after")
    return self
```

---

## How to Use Context7 in Practice

```
When you write code involving any library:

1. STOP before writing
2. Run: npx ctx7@latest library "<library>" "<your question>"
3. Pick the best match by description + code snippet count
4. Run: npx ctx7@latest docs <libraryId> "<specific question>"
5. THEN write the code using the fetched docs

Never rely on training data for:
- Method signatures (they change)
- Option names (they change between major versions)
- Default values (they change)
- Deprecated APIs (you won't know they're deprecated)
```
