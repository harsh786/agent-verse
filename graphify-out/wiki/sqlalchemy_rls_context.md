# sqlalchemy_rls_context()

> God node · 200 connections · `agent-verse-backend/app/db/rls.py`

**Community:** [Artifacts API & Coordination](Artifacts_API_&_Coordination.md)

## Connections by Relation

### calls
- voice_greeting() `EXTRACTED`
- ._tenant_session() `EXTRACTED`
- ._db_get_goal_record() `EXTRACTED`
- get_goal_traces() `EXTRACTED`
- .hybrid_search_db() `EXTRACTED`
- submit_goal_feedback() `EXTRACTED`
- .transition() `EXTRACTED`
- .score_and_persist() `EXTRACTED`
- ._expand_agentic_parent_citations() `EXTRACTED`
- _load_db_schedules() `EXTRACTED`
- voice_stream() `EXTRACTED`
- .list_async() `EXTRACTED`
- get_artifact() `EXTRACTED`
- list_artifacts() `EXTRACTED`
- get_connector_health_history() `EXTRACTED`
- record_consent() `EXTRACTED`
- request_erasure() `EXTRACTED`
- get_goal_attempts() `EXTRACTED`
- get_goal_lineage() `EXTRACTED`
- set_template_price() `EXTRACTED`
- *…and 127 more `calls` connection(s) not listed (lowest-degree first to go)*

### contains
- rls.py `EXTRACTED`

### imports
- tasks.py `EXTRACTED`
- api/knowledge.py `EXTRACTED`
- org/router.py `EXTRACTED`
- goal_service.py `EXTRACTED`
- rag/gateway.py `EXTRACTED`
- api/governance.py `EXTRACTED`
- connectors.py `EXTRACTED`
- agent/graph.py `EXTRACTED`
- agents.py `EXTRACTED`
- goals.py `EXTRACTED`
- tenants.py `EXTRACTED`
- api/civilization.py `EXTRACTED`
- voice/router.py `EXTRACTED`
- schedules.py `EXTRACTED`
- rag/store.py `EXTRACTED`
- raft_repository.py `EXTRACTED`
- coordination/store.py `EXTRACTED`
- handoffs/repository.py `EXTRACTED`
- policies.py `EXTRACTED`
- postgres_repository.py `EXTRACTED`
- *…and 29 more `imports` connection(s) not listed (lowest-degree first to go)*

### indirect_call
- _get_rls_imports() `INFERRED`

### rationale_for
- Set app.tenant_id RLS variable for a SQLAlchemy AsyncSession. Must be called… `EXTRACTED`

### references
- AsyncSession `EXTRACTED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*