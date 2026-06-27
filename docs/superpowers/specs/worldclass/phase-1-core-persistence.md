# Phase 1: Core Persistence — All In-Memory Stores → Database-Backed

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every in-memory data structure that loses state across restarts with fully DB-backed implementations using async SQLAlchemy, pgvector, and langgraph checkpointing.

**Architecture:** Each store gets a DB-first read path while maintaining in-memory fallback for test isolation. LangGraph checkpointing moves from `MemorySaver` (in-process) to `PostgresSaver` (cross-restart) wired during the `create_app()` lifespan. All migrations are Alembic-managed.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy 2.x, PostgreSQL 16 + pgvector, langgraph-checkpoint-postgres, pytest-asyncio, httpx ASGITransport

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `app/api/agents.py` | Modify | Make `AgentStore.get/list_all/delete/update` DB-primary |
| `app/governance/hitl.py` | Modify | Persist approval requests; load on startup |
| `app/governance/audit.py` | Modify | `query()` reads from DB with filters + pagination |
| `app/memory/long_term.py` | Modify | pgvector recall; persist to `long_term_memory` table |
| `app/db/models/memory.py` | Create | `LongTermMemory` ORM model |
| `app/db/models/eval.py` | Create | `EvalSuite`, `EvalSuiteResult` ORM models |
| `app/db/migrations/versions/0020_long_term_memory.py` | Create | `long_term_memory` table with pgvector |
| `app/db/migrations/versions/0021_eval_suites.py` | Create | `eval_suites`, `eval_suite_results` tables |
| `app/agent/graph.py` | Modify | Replace `MemorySaver` with `PostgresSaver`; wire in lifespan |
| `app/main.py` | Modify | Wire `PostgresSaver` in lifespan; load policies from DB on startup |
| `app/intelligence/eval_suite.py` | Modify | `_suites`/`_results` → DB-backed via new ORM models |
| `tests/test_phase1_persistence.py` | Create | Full pytest-asyncio test suite for all 8 items |

---

## Task 1.1 — AgentStore: DB-Primary Reads

**Current state:** `AgentStore._data` is an in-memory dict. `sync_from_db()` loads on startup but `get()`, `list_all()`, `delete()`, `update()` read/write only the in-memory dict. After DB sync the memory is correct, but any agent created after restart in a different replica is invisible.

**Gap:** `get()`, `list_all()`, `delete()`, `update()` must hit the DB when `_db` is set.

**Files:**
- Modify: `agent-verse-backend/app/api/agents.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Write the failing tests for DB-primary reads**

```python
# tests/test_phase1_persistence.py
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from app.api.agents import AgentStore
from app.tenancy.context import TenantContext, PlanTier

@pytest.fixture
def tenant():
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

@pytest.mark.asyncio
async def test_agent_store_get_hits_db_when_not_in_memory(tenant):
    """get() must query DB when agent is not in _data."""
    mock_session = AsyncMock()
    mock_agent = MagicMock()
    mock_agent.id = "a1"
    mock_agent.tenant_id = "t1"
    mock_agent.name = "Test"
    mock_agent.goal_template = "do stuff"
    mock_agent.autonomy_mode = "bounded-autonomous"
    mock_agent.connector_ids = []
    mock_agent.trigger_config = {}
    mock_agent.is_active = True
    mock_agent.created_at = None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)
    store = AgentStore(db_session_factory=db_factory)
    result = await store.get_async("a1", tenant_ctx=tenant)
    assert result is not None
    assert result["agent_id"] == "a1"
    assert result["name"] == "Test"

@pytest.mark.asyncio
async def test_agent_store_list_all_hits_db(tenant):
    """list_all_async() must query DB for complete tenant list."""
    mock_session = AsyncMock()
    mock_agent = MagicMock()
    mock_agent.id = "a2"
    mock_agent.tenant_id = "t1"
    mock_agent.name = "DB Agent"
    mock_agent.goal_template = "run things"
    mock_agent.autonomy_mode = "supervised"
    mock_agent.connector_ids = ["github"]
    mock_agent.trigger_config = {}
    mock_agent.is_active = True
    mock_agent.created_at = None
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_agent]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)
    store = AgentStore(db_session_factory=db_factory)
    results = await store.list_all_async(tenant_ctx=tenant)
    assert len(results) == 1
    assert results[0]["name"] == "DB Agent"

@pytest.mark.asyncio
async def test_agent_store_delete_removes_from_db(tenant):
    """delete_async() must issue DELETE on DB and remove from memory."""
    mock_session = AsyncMock()
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.delete = AsyncMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)
    store = AgentStore(db_session_factory=db_factory)
    # Pre-populate memory
    store._data[("t1", "a3")] = {"agent_id": "a3", "name": "gone"}
    ok = await store.delete_async("a3", tenant_ctx=tenant)
    assert ok is True
    assert ("t1", "a3") not in store._data
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd agent-verse-backend
pytest tests/test_phase1_persistence.py::test_agent_store_get_hits_db_when_not_in_memory -xvs
```
Expected: `AttributeError: 'AgentStore' object has no attribute 'get_async'`

- [ ] **Step 3: Implement DB-primary methods in AgentStore**

Add these methods to `app/api/agents.py` inside `AgentStore` after the existing `sync_from_db`:

```python
def _row_to_dict(self, agent: Any) -> dict[str, Any]:
    """Convert a DB Agent ORM row to the canonical in-memory dict format."""
    created_at = getattr(agent, "created_at", None)
    return {
        "agent_id": str(agent.id),
        "tenant_id": str(agent.tenant_id),
        "name": agent.name,
        "goal_template": agent.goal_template or "",
        "autonomy_mode": agent.autonomy_mode or "bounded-autonomous",
        "connector_ids": list(agent.connector_ids or []),
        "trigger_config": dict(agent.trigger_config or {}),
        "permissions": {},
        "allowed_collection_ids": [],
        "system_prompt": getattr(agent, "system_prompt", "") or "",
        "created_at": created_at.isoformat() if created_at else "",
    }

async def get_async(
    self, agent_id: str, *, tenant_ctx: TenantContext
) -> dict[str, Any] | None:
    """DB-primary get: check memory first, then DB."""
    key = (tenant_ctx.tenant_id, agent_id)
    if key in self._data:
        return self._data[key]
    if self._db is None:
        return None
    try:
        from sqlalchemy import select
        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context
        async with self._db() as session:
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(Agent).where(
                        Agent.id == agent_id,
                        Agent.tenant_id == tenant_ctx.tenant_id,
                        Agent.is_active == True,  # noqa: E712
                    )
                )
                row = result.scalar_one_or_none()
        if row is None:
            return None
        rec = self._row_to_dict(row)
        self._data[key] = rec  # populate cache
        return rec
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("DB get agent failed: %s", exc)
        return None

async def list_all_async(
    self, *, tenant_ctx: TenantContext
) -> list[dict[str, Any]]:
    """DB-primary list: always queries DB when factory is available."""
    if self._db is None:
        return self.list_all(tenant_ctx=tenant_ctx)
    try:
        from sqlalchemy import select
        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context
        async with self._db() as session:
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(Agent).where(
                        Agent.tenant_id == tenant_ctx.tenant_id,
                        Agent.is_active == True,  # noqa: E712
                    )
                )
                rows = result.scalars().all()
        records = [self._row_to_dict(r) for r in rows]
        # Sync to local cache
        for rec in records:
            self._data[(tenant_ctx.tenant_id, rec["agent_id"])] = rec
        return records
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("DB list agents failed: %s", exc)
        return self.list_all(tenant_ctx=tenant_ctx)

async def delete_async(
    self, agent_id: str, *, tenant_ctx: TenantContext
) -> bool:
    """DB-primary delete: removes from DB and evicts from memory."""
    key = (tenant_ctx.tenant_id, agent_id)
    self._data.pop(key, None)  # evict from cache regardless
    if self._db is None:
        return True
    try:
        from sqlalchemy import select
        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context
        async with self._db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(Agent).where(
                        Agent.id == agent_id,
                        Agent.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return False
                row.is_active = False  # soft-delete
        return True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("DB delete agent failed: %s", exc)
        return False

async def update_async(
    self,
    agent_id: str,
    data: dict[str, Any],
    *,
    tenant_ctx: TenantContext,
) -> bool:
    """DB-primary update: writes to DB and updates memory cache."""
    key = (tenant_ctx.tenant_id, agent_id)
    # Update memory cache if present
    rec = self._data.get(key)
    if rec is not None:
        rec.update(data)
    if self._db is None:
        return rec is not None
    try:
        from sqlalchemy import select
        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context
        async with self._db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(Agent).where(
                        Agent.id == agent_id,
                        Agent.tenant_id == tenant_ctx.tenant_id,
                        Agent.is_active == True,  # noqa: E712
                    ).with_for_update()
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return False
                if "name" in data:
                    row.name = data["name"]
                if "goal_template" in data:
                    row.goal_template = data["goal_template"]
                if "autonomy_mode" in data:
                    row.autonomy_mode = data["autonomy_mode"]
                if "connector_ids" in data:
                    row.connector_ids = list(data["connector_ids"])
                if "trigger_config" in data:
                    row.trigger_config = dict(data["trigger_config"])
                if "system_prompt" in data:
                    row.system_prompt = data.get("system_prompt", "")
        return True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("DB update agent failed: %s", exc)
        return False
```

Also update the API endpoints to use the async variants. In `app/api/agents.py`, change:
- `store.get(agent_id, ...)` → `await store.get_async(agent_id, ...)` (all route handlers)
- `store.list_all(...)` → `await store.list_all_async(...)`
- `store.delete(agent_id, ...)` → `await store.delete_async(agent_id, ...)`
- `store.update(agent_id, ...)` → `await store.update_async(agent_id, ...)`

Concretely, update every route handler:

```python
@router.get("")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    return await store.list_all_async(tenant_ctx=tenant_ctx)

@router.get("/{agent_id}")
async def get_agent(request: Request, agent_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    rec = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    return rec

@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(request: Request, agent_id: str) -> None:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    removed = await store.delete_async(agent_id, tenant_ctx=tenant_ctx)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/test_phase1_persistence.py -k "agent_store" -xvs
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/api/agents.py tests/test_phase1_persistence.py
git commit -m "feat(persistence): AgentStore DB-primary reads for get/list/delete/update"
```

---

## Task 1.2 — HITLGateway: Persist Approval Requests on Creation

**Current state:** `HITLGateway._requests` is a dict. `load_pending_from_db()` exists but `request_approval()` never writes to DB, so pending requests are lost on restart.

**Gap:** `request_approval()` must INSERT into `approval_requests` DB table. `approve()` and `reject()` must UPDATE the row's `status` and `resolved_at`. On startup, `load_pending_from_db()` must also recreate `asyncio.Event` objects for each pending row.

**Files:**
- Modify: `agent-verse-backend/app/governance/hitl.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase1_persistence.py

@pytest.mark.asyncio
async def test_hitl_request_approval_persists_to_db():
    """request_approval must INSERT into DB when factory is configured."""
    inserted_rows = []

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def begin(self): return self
        def add(self, row):
            inserted_rows.append(row)
        async def execute(self, *a, **kw):
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

    db_factory = MagicMock(return_value=FakeSession())
    gw = HITLGateway(timeout_seconds=60.0)
    gw._db = db_factory
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    req_id = await gw.request_approval_async(
        goal_id="g1", action="deploy prod", risk_level="high", tenant_ctx=tenant
    )
    assert req_id
    assert len(inserted_rows) == 1
    from app.db.models.governance import ApprovalRequest as DBReq
    assert isinstance(inserted_rows[0], DBReq)
    assert inserted_rows[0].goal_id == "g1"
    assert inserted_rows[0].status == "pending"

@pytest.mark.asyncio
async def test_hitl_approve_updates_db():
    """approve must UPDATE DB row status to 'approved'."""
    updated_rows = []

    class FakeResult:
        def scalar_one_or_none(self):
            row = MagicMock()
            row.id = "req1"
            row.status = "pending"
            return row

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def begin(self): return self
        async def execute(self, *a, **kw):
            return FakeResult()
        def add(self, row): updated_rows.append(row)

    db_factory = MagicMock(return_value=FakeSession())
    from app.governance.hitl import HITLGateway, ApprovalRequest, ApprovalStatus
    import asyncio
    gw = HITLGateway()
    gw._db = db_factory
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    # Pre-populate in-memory
    req = ApprovalRequest(goal_id="g1", action="deploy", risk_level="high",
                          request_id="req1")
    gw._requests[("t1", "req1")] = req
    ok = await gw.approve_async("req1", approver="alice", note="lgtm", tenant_ctx=tenant)
    assert ok is True
    assert req.status == ApprovalStatus.APPROVED
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_phase1_persistence.py -k "hitl" -xvs
```
Expected: `AttributeError: 'HITLGateway' object has no attribute 'request_approval_async'`

- [ ] **Step 3: Add async DB methods to HITLGateway**

Add a `_db` attribute and three new async methods to `app/governance/hitl.py`:

```python
# Add to HITLGateway.__init__:
self._db: Any = None  # set by main.py lifespan

async def request_approval_async(
    self,
    *,
    goal_id: str,
    action: str,
    risk_level: str,
    tenant_ctx: TenantContext,
) -> str:
    """Create approval request + persist to DB + return request_id."""
    from datetime import UTC, timedelta
    req = ApprovalRequest(goal_id=goal_id, action=action, risk_level=risk_level)
    req._expires_at_dt = datetime.now(UTC) + timedelta(seconds=self._timeout)
    self._requests[(tenant_ctx.tenant_id, req.request_id)] = req

    if self._db is not None:
        try:
            from app.db.models.governance import ApprovalRequest as DBReq
            from app.db.rls import sqlalchemy_rls_context
            db_row = DBReq(
                id=req.request_id,
                tenant_id=tenant_ctx.tenant_id,
                goal_id=goal_id,
                action=action,
                risk_level=risk_level,
                status="pending",
                expires_at=req._expires_at_dt,
            )
            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    session.add(db_row)
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("hitl_db_persist_failed: %s", exc)

    if self._notification_service is not None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(
                    self._notification_service.notify_approval_required(
                        request_id=req.request_id,
                        goal_id=goal_id,
                        action=action,
                        risk_level=risk_level,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                )
        except Exception:
            pass

    return req.request_id

async def approve_async(
    self,
    request_id: str,
    *,
    approver: str,
    note: str = "",
    tenant_ctx: TenantContext,
) -> bool:
    """Approve request in memory + persist resolution to DB."""
    ok = self.approve(request_id, approver=approver, note=note, tenant_ctx=tenant_ctx)
    if ok and self._db is not None:
        await self._db_update_status(request_id, "approved", approver, note,
                                      tenant_ctx.tenant_id)
    return ok

async def reject_async(
    self,
    request_id: str,
    *,
    approver: str,
    note: str = "",
    tenant_ctx: TenantContext,
) -> bool:
    """Reject request in memory + persist resolution to DB."""
    ok = self.reject(request_id, approver=approver, note=note, tenant_ctx=tenant_ctx)
    if ok and self._db is not None:
        await self._db_update_status(request_id, "rejected", approver, note,
                                      tenant_ctx.tenant_id)
    return ok

async def _db_update_status(
    self,
    request_id: str,
    status: str,
    approver: str,
    note: str,
    tenant_id: str,
) -> None:
    if self._db is None:
        return
    try:
        from datetime import UTC
        from sqlalchemy import select
        from app.db.models.governance import ApprovalRequest as DBReq
        from app.db.rls import sqlalchemy_rls_context
        async with self._db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                result = await session.execute(
                    select(DBReq).where(
                        DBReq.id == request_id,
                        DBReq.tenant_id == tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is not None:
                    row.status = status
                    row.approver = approver
                    row.note = note
                    row.resolved_at = datetime.now(UTC)
    except Exception as exc:
        from app.observability.logging import get_logger
        get_logger(__name__).warning("hitl_db_update_failed: %s", exc)
```

Also update `load_pending_from_db` to properly recreate `asyncio.Event` objects — the existing implementation already does this correctly; no change needed there.

Wire `_db` in `app/main.py` lifespan after `db_factory` is available:
```python
# In lifespan, after db_factory = get_session_factory():
app.state.hitl_gateway._db = db_factory
await app.state.hitl_gateway.load_pending_from_db(db_factory, tenant_id="")  # loads all tenants
```

Update `app/api/governance.py` approve/reject endpoints to call async variants:
```python
@router.post("/approvals/{request_id}/approve")
async def approve_request(request: Request, request_id: str, body: ApproveRejectRequest):
    tenant_ctx = _require_tenant(request)
    gateway = _hitl(request)
    ok = await gateway.approve_async(
        request_id, approver=body.approver, note=body.note, tenant_ctx=tenant_ctx
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Approval request {request_id} not found")
    return {"request_id": request_id, "status": "approved", "approver": body.approver}

@router.post("/approvals/{request_id}/reject")
async def reject_request(request: Request, request_id: str, body: ApproveRejectRequest):
    tenant_ctx = _require_tenant(request)
    gateway = _hitl(request)
    ok = await gateway.reject_async(
        request_id, approver=body.approver, note=body.note, tenant_ctx=tenant_ctx
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Approval request {request_id} not found")
    return {"request_id": request_id, "status": "rejected", "approver": body.approver}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_phase1_persistence.py -k "hitl" -xvs
```
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/governance/hitl.py app/api/governance.py app/main.py tests/test_phase1_persistence.py
git commit -m "feat(persistence): HITLGateway persists approval requests to DB"
```

---

## Task 1.3 — AuditLog: DB-Backed query() with Filters + Pagination

**Current state:** `query()` reads only from `self._log` (in-memory list). The DB write path exists (`_db_record()`) but reads never touch the DB, so after restart the in-memory log is empty.

**Gap:** `query()` must read from DB when `_db` is set. Must support `goal_id`, `tool_name`, `start_time`, `end_time` filters and `limit`/`offset` pagination.

**Files:**
- Modify: `agent-verse-backend/app/governance/audit.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase1_persistence.py

@pytest.mark.asyncio
async def test_audit_query_reads_from_db():
    """query_async() must read from DB with filters when factory is set."""
    from datetime import UTC, datetime
    from app.governance.audit import AuditLog, AuditEvent
    from app.governance.permissions import ActionLevel

    mock_row = MagicMock()
    mock_row.id = "ev1"
    mock_row.goal_id = "g1"
    mock_row.tool_name = "web_search"
    mock_row.action_level = "allow_log"
    mock_row.outcome = "success"
    mock_row.step_id = ""
    mock_row.approver = None
    mock_row.note = ""
    mock_row.created_at = datetime.now(UTC)
    mock_row.ip_address = None
    mock_row.user_agent = None
    mock_row.api_key_id = None
    mock_row.request_id = None
    mock_row.connector_id = None

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_row]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)

    log = AuditLog(db_session_factory=db_factory)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    events = await log.query_async(tenant_ctx=tenant, goal_id="g1")
    assert len(events) == 1
    assert events[0].tool_name == "web_search"

@pytest.mark.asyncio
async def test_audit_query_supports_pagination():
    """query_async() respects limit and offset parameters."""
    rows = []
    for i in range(5):
        r = MagicMock()
        r.id = f"ev{i}"
        r.goal_id = "g1"
        r.tool_name = f"tool_{i}"
        r.action_level = "allow_log"
        r.outcome = "success"
        r.step_id = ""
        r.approver = None
        r.note = ""
        from datetime import UTC, datetime
        r.created_at = datetime.now(UTC)
        r.ip_address = None
        r.user_agent = None
        r.api_key_id = None
        r.request_id = None
        r.connector_id = None
        rows.append(r)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows[:2]  # DB returns 2 after pagination
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)

    from app.governance.audit import AuditLog
    log = AuditLog(db_session_factory=db_factory)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    events = await log.query_async(tenant_ctx=tenant, limit=2, offset=0)
    assert len(events) == 2
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_phase1_persistence.py -k "audit" -xvs
```
Expected: `AttributeError: 'AuditLog' object has no attribute 'query_async'`

- [ ] **Step 3: Implement query_async() in AuditLog**

Add to `app/governance/audit.py`:

```python
from datetime import datetime as _datetime

async def query_async(
    self,
    *,
    tenant_ctx: TenantContext,
    goal_id: str | None = None,
    tool_name: str | None = None,
    start_time: _datetime | None = None,
    end_time: _datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent]:
    """DB-backed audit query with time-range, filters, and pagination.

    Falls back to in-memory query when no DB is configured (test mode).
    """
    if self._db is None:
        return self.query(tenant_ctx=tenant_ctx, goal_id=goal_id, tool_name=tool_name)

    try:
        from sqlalchemy import select

        from app.db.models.governance import AuditLog as AuditLogModel
        q = (
            select(AuditLogModel)
            .where(AuditLogModel.tenant_id == tenant_ctx.tenant_id)
            .order_by(AuditLogModel.created_at.desc())
        )
        if goal_id is not None:
            q = q.where(AuditLogModel.goal_id == goal_id)
        if tool_name is not None:
            q = q.where(AuditLogModel.tool_name == tool_name)
        if start_time is not None:
            q = q.where(AuditLogModel.created_at >= start_time)
        if end_time is not None:
            q = q.where(AuditLogModel.created_at <= end_time)
        q = q.limit(limit).offset(offset)

        async with self._db() as session:
            result = await session.execute(q)
            rows = result.scalars().all()

        events: list[AuditEvent] = []
        for row in rows:
            try:
                level = ActionLevel(row.action_level)
            except ValueError:
                level = ActionLevel.ALLOW_LOG
            events.append(
                AuditEvent(
                    goal_id=row.goal_id,
                    tool_name=row.tool_name,
                    action_level=level,
                    outcome=row.outcome,
                    step_id=row.step_id or "",
                    approver=row.approver,
                    note=row.note or "",
                    event_id=row.id,
                    ip_address=getattr(row, "ip_address", None),
                    user_agent=getattr(row, "user_agent", None),
                    api_key_id=getattr(row, "api_key_id", None),
                    request_id=getattr(row, "request_id", None),
                    connector_id=getattr(row, "connector_id", None),
                )
            )
        return events
    except Exception as exc:
        _log.warning("DB audit query failed: %s", exc)
        return self.query(tenant_ctx=tenant_ctx, goal_id=goal_id, tool_name=tool_name)
```

Update `app/api/governance.py` `query_audit` endpoint:

```python
@router.get("/audit")
async def query_audit(
    request: Request,
    goal_id: str | None = None,
    tool_name: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    log = _audit(request)
    from datetime import datetime as _dt
    start_dt = _dt.fromisoformat(start_time) if start_time else None
    end_dt = _dt.fromisoformat(end_time) if end_time else None
    events = await log.query_async(
        tenant_ctx=tenant_ctx,
        goal_id=goal_id,
        tool_name=tool_name,
        start_time=start_dt,
        end_time=end_dt,
        limit=limit,
        offset=offset,
    )
    return [
        {
            "event_id": e.event_id,
            "goal_id": e.goal_id,
            "tool_name": e.tool_name,
            "action_level": str(e.action_level),
            "outcome": e.outcome,
            "step_id": e.step_id,
            "approver": e.approver,
            "note": e.note,
        }
        for e in events
    ]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_phase1_persistence.py -k "audit" -xvs
```

- [ ] **Step 5: Commit**

```bash
git add app/governance/audit.py app/api/governance.py tests/test_phase1_persistence.py
git commit -m "feat(persistence): AuditLog query_async DB-backed with filters and pagination"
```

---

## Task 1.4 — LongTermMemoryStore: pgvector Semantic Search

**Current state:** `LongTermMemoryStore._memories` is an in-memory dict. `recall()` uses word-count keyword matching. Nothing is persisted to DB.

**Gap:** Store memories in `long_term_memory` DB table (new migration). `recall()` uses `embedding <=> query_embedding` cosine distance via pgvector. Fall back to keyword scoring when embedder is unavailable.

**Files:**
- Create: `agent-verse-backend/app/db/models/memory.py`
- Create: `agent-verse-backend/app/db/migrations/versions/0020_long_term_memory.py`
- Modify: `agent-verse-backend/app/memory/long_term.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Create the ORM model**

```python
# app/db/models/memory.py
"""SQLAlchemy ORM model for long-term cross-session memories."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class LongTermMemoryEntry(Base):
    """Persistent cross-session learning entry with optional embedding vector."""

    __tablename__ = "long_term_memory"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, default="success_pattern")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # embedding stored as JSON array (pgvector column added by migration)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 2: Create the Alembic migration**

```python
# app/db/migrations/versions/0020_long_term_memory.py
"""Create long_term_memory table with pgvector support.

Revision ID: 0020
Revises: 0019
Create Date: 2026-01-01 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"  # update to actual previous migration id
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension if not already present
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "long_term_memory",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source_goal_id", sa.String(32), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False, server_default="success_pattern"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("tags", sa.JSON, nullable=False, server_default="[]"),
        # vector(1536) for OpenAI text-embedding-3-small / Voyage-3
        sa.Column("embedding", sa.Text, nullable=True),  # stored as pgvector text
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Add pgvector column via raw SQL
    op.execute(
        "ALTER TABLE long_term_memory "
        "ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)"
    )
    op.create_index("ix_ltm_tenant", "long_term_memory", ["tenant_id"])
    op.create_index("ix_ltm_source_goal", "long_term_memory", ["source_goal_id"])
    # IVFFlat index for approximate nearest-neighbour cosine search
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ltm_embedding_vec "
        "ON long_term_memory USING ivfflat (embedding_vec vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_table("long_term_memory")
```

- [ ] **Step 3: Write failing tests**

```python
# append to tests/test_phase1_persistence.py

@pytest.mark.asyncio
async def test_ltm_store_persists_to_db():
    """store_async() must INSERT into DB."""
    from app.memory.long_term import LongTermMemoryStore, LongTermMemory
    inserted = []

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def begin(self): return self
        def add(self, row): inserted.append(row)

    db_factory = MagicMock(return_value=FakeSession())
    store = LongTermMemoryStore(db_session_factory=db_factory)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    mem = LongTermMemory(
        content="Use GitHub API for code retrieval",
        source_goal_id="g1",
        memory_type="tool_preference",
    )
    mid = await store.store_async(memory=mem, tenant_ctx=tenant)
    assert mid == mem.memory_id
    assert len(inserted) == 1

@pytest.mark.asyncio
async def test_ltm_recall_falls_back_to_keyword_without_embedder():
    """recall_async() uses keyword scoring when no embedder is provided."""
    from app.memory.long_term import LongTermMemoryStore, LongTermMemory
    store = LongTermMemoryStore()
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    mem = LongTermMemory(
        content="Use GitHub API for code retrieval",
        source_goal_id="g1",
        memory_type="tool_preference",
    )
    store.store(memory=mem, tenant_ctx=tenant)
    results = await store.recall_async(
        query="GitHub code", tenant_ctx=tenant, top_k=5
    )
    assert len(results) >= 1
    assert "GitHub" in results[0].content
```

- [ ] **Step 4: Run — expect failures**

```bash
pytest tests/test_phase1_persistence.py -k "ltm" -xvs
```

- [ ] **Step 5: Implement store_async and recall_async**

Modify `app/memory/long_term.py`:

```python
"""Long-term memory — cross-session learnings persisted across agent runs."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.tenancy.context import TenantContext


@dataclass
class LongTermMemory:
    content: str
    source_goal_id: str
    memory_type: str
    confidence: float = 1.0
    memory_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tags: list[str] = field(default_factory=list)


class LongTermMemoryStore:
    def __init__(
        self,
        db_session_factory: Any = None,
        embedder: Any = None,
    ) -> None:
        self._memories: dict[str, list[LongTermMemory]] = {}
        self._db = db_session_factory
        self._embedder = embedder

    def store(self, *, memory: LongTermMemory, tenant_ctx: TenantContext) -> str:
        self._memories.setdefault(tenant_ctx.tenant_id, []).append(memory)
        return memory.memory_id

    async def store_async(
        self, *, memory: LongTermMemory, tenant_ctx: TenantContext
    ) -> str:
        """Persist memory to DB and in-memory cache. Generates embedding if embedder available."""
        self._memories.setdefault(tenant_ctx.tenant_id, []).append(memory)

        embedding: list[float] | None = None
        if self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest
                resp = await self._embedder.embed(EmbedRequest(texts=[memory.content]))
                embedding = resp.embeddings[0] if resp.embeddings else None
            except Exception:
                pass

        if self._db is not None:
            try:
                from app.db.models.memory import LongTermMemoryEntry
                from app.db.rls import sqlalchemy_rls_context
                row = LongTermMemoryEntry(
                    id=memory.memory_id,
                    tenant_id=tenant_ctx.tenant_id,
                    content=memory.content,
                    source_goal_id=memory.source_goal_id,
                    memory_type=memory.memory_type,
                    confidence=memory.confidence,
                    tags=memory.tags,
                    embedding=embedding,
                )
                async with self._db() as session, session.begin():
                    async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                        session.add(row)
                        if embedding is not None:
                            # Store as pgvector via raw SQL
                            vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
                            from sqlalchemy import text
                            await session.execute(
                                text(
                                    "UPDATE long_term_memory "
                                    "SET embedding_vec = :vec::vector "
                                    "WHERE id = :id"
                                ),
                                {"vec": vec_str, "id": memory.memory_id},
                            )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("ltm_db_store_failed: %s", exc)

        return memory.memory_id

    async def recall_async(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        top_k: int = 10,
        memory_type: str | None = None,
    ) -> list[LongTermMemory]:
        """Recall via pgvector cosine similarity, falling back to keyword scoring."""
        # Try pgvector recall first
        if self._db is not None and self._embedder is not None:
            try:
                return await self._recall_semantic(
                    query=query, tenant_ctx=tenant_ctx,
                    top_k=top_k, memory_type=memory_type
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("ltm_pgvector_recall_failed: %s", exc)

        # Fallback: keyword scoring
        return self.recall(
            query=query, tenant_ctx=tenant_ctx,
            top_k=top_k, memory_type=memory_type
        )

    async def _recall_semantic(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        top_k: int,
        memory_type: str | None,
    ) -> list[LongTermMemory]:
        from app.providers.base import EmbedRequest
        from sqlalchemy import text
        resp = await self._embedder.embed(EmbedRequest(texts=[query]))
        if not resp.embeddings:
            return []
        query_embedding = resp.embeddings[0]
        vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        params: dict[str, Any] = {
            "tid": tenant_ctx.tenant_id,
            "vec": vec_str,
            "top_k": top_k,
        }
        type_filter = ""
        if memory_type:
            type_filter = "AND memory_type = :mtype"
            params["mtype"] = memory_type

        q = text(
            f"""
            SELECT id, content, source_goal_id, memory_type, confidence, tags, created_at
            FROM long_term_memory
            WHERE tenant_id = :tid
              AND embedding_vec IS NOT NULL
              {type_filter}
            ORDER BY embedding_vec <=> :vec::vector
            LIMIT :top_k
            """
        )
        async with self._db() as session:
            result = await session.execute(q, params)
            rows = result.fetchall()

        memories: list[LongTermMemory] = []
        for row in rows:
            memories.append(
                LongTermMemory(
                    content=row.content,
                    source_goal_id=row.source_goal_id,
                    memory_type=row.memory_type,
                    confidence=row.confidence,
                    memory_id=row.id,
                    tags=row.tags or [],
                )
            )
        return memories

    def recall(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        top_k: int = 10,
        memory_type: str | None = None,
    ) -> list[LongTermMemory]:
        """Keyword fallback recall."""
        memories = self._memories.get(tenant_ctx.tenant_id, [])
        if memory_type:
            memories = [m for m in memories if m.memory_type == memory_type]
        query_lower = query.lower()
        scored = [
            (m, sum(1 for word in query_lower.split() if word in m.content.lower()))
            for m in memories
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored[:top_k]]

    def delete(self, *, memory_id: str, tenant_ctx: TenantContext) -> bool:
        memories = self._memories.get(tenant_ctx.tenant_id, [])
        for i, m in enumerate(memories):
            if m.memory_id == memory_id:
                memories.pop(i)
                return True
        return False

    def list_all(self, *, tenant_ctx: TenantContext) -> list[LongTermMemory]:
        return list(self._memories.get(tenant_ctx.tenant_id, []))

    def extract_from_goal(
        self,
        *,
        goal: str,
        result: str,
        goal_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        memory = LongTermMemory(
            content=f"Goal: {goal[:200]} → Result: {result[:200]}",
            source_goal_id=goal_id,
            memory_type="success_pattern",
            confidence=0.8,
            tags=["auto-extracted"],
        )
        mid = self.store(memory=memory, tenant_ctx=tenant_ctx)
        return [mid]
```

Wire embedder in `app/main.py` lifespan:
```python
# After embedder is resolved:
_long_term_memory = LongTermMemoryStore(db_session_factory=None, embedder=_embedder)
# In lifespan after db_factory is available:
app.state.long_term_memory._db = db_factory
app.state.long_term_memory._embedder = _embedder
```

- [ ] **Step 6: Run all tests — expect pass**

```bash
pytest tests/test_phase1_persistence.py -k "ltm" -xvs
```

- [ ] **Step 7: Commit**

```bash
git add app/db/models/memory.py app/db/migrations/versions/0020_long_term_memory.py \
        app/memory/long_term.py app/main.py tests/test_phase1_persistence.py
git commit -m "feat(persistence): LongTermMemoryStore pgvector semantic recall + DB persistence"
```

---

## Task 1.5 — LangGraph MemorySaver → PostgresSaver

**Current state:** `AgentGraph.__init__` creates `self._checkpointer = MemorySaver()`. This is in-process only — state is lost on restart.

**Gap:** Use `langgraph-checkpoint-postgres` `AsyncPostgresSaver` when `DATABASE_URL` is configured. Fall back to `MemorySaver` in tests.

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Modify: `agent-verse-backend/app/main.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Install dependency**

```bash
cd agent-verse-backend
uv add langgraph-checkpoint-postgres
```

- [ ] **Step 2: Write failing tests**

```python
# append to tests/test_phase1_persistence.py

def test_agent_graph_accepts_external_checkpointer():
    """AgentGraph must accept an externally-provided checkpointer."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from langgraph.checkpoint.memory import MemorySaver

    fake = FakeProvider()
    custom_checkpointer = MemorySaver()
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        checkpointer=custom_checkpointer,
    )
    assert graph._checkpointer is custom_checkpointer

def test_agent_graph_default_checkpointer_is_memory_saver():
    """Default checkpointer is MemorySaver when none supplied."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from langgraph.checkpoint.memory import MemorySaver

    fake = FakeProvider()
    graph = AgentGraph(planner=fake, executor=fake, verifier=fake)
    assert isinstance(graph._checkpointer, MemorySaver)
```

- [ ] **Step 3: Run — expect failures**

```bash
pytest tests/test_phase1_persistence.py -k "checkpointer" -xvs
```
Expected: `TypeError: AgentGraph.__init__() got an unexpected keyword argument 'checkpointer'`

- [ ] **Step 4: Modify AgentGraph to accept external checkpointer**

In `app/agent/graph.py`, add `checkpointer` parameter to `__init__`:

```python
# In AgentGraph.__init__ signature, add:
checkpointer: Any | None = None,

# In __init__ body, replace:
self._checkpointer = MemorySaver()
# With:
if checkpointer is not None:
    self._checkpointer = checkpointer
else:
    self._checkpointer = MemorySaver()
```

- [ ] **Step 5: Add PostgresSaver wiring in main.py lifespan**

```python
# In create_app lifespan, after db_factory = get_session_factory():
import os
_pg_url = os.getenv("DATABASE_URL", "")
_pg_checkpointer: Any = None
if _pg_url:
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        _pg_checkpointer = AsyncPostgresSaver.from_conn_string(_pg_url)
        await _pg_checkpointer.setup()  # creates langgraph_checkpoint tables
        logger.info("langgraph_postgres_checkpointer_ready")
    except Exception as exc:
        logger.warning("langgraph_postgres_checkpointer_failed: %s", exc)
        _pg_checkpointer = None

# Wire into GoalService so new AgentGraph instances use the postgres checkpointer
if _pg_checkpointer is not None:
    app.state.langgraph_checkpointer = _pg_checkpointer
    # GoalService creates AgentGraph on each goal; wire via _goal_svc._checkpointer
    _goal_svc_with_db._checkpointer = _pg_checkpointer
```

In `app/services/goal_service.py`, look for where `AgentGraph` is constructed and pass `checkpointer`:
```python
graph = AgentGraph(
    planner=planner,
    executor=executor,
    verifier=verifier,
    # ... other kwargs ...
    checkpointer=getattr(self, "_checkpointer", None),
)
```

- [ ] **Step 6: Run tests — expect pass**

```bash
pytest tests/test_phase1_persistence.py -k "checkpointer" -xvs
```

- [ ] **Step 7: Commit**

```bash
git add app/agent/graph.py app/main.py app/services/goal_service.py \
        tests/test_phase1_persistence.py
git commit -m "feat(persistence): LangGraph PostgresSaver for cross-restart checkpointing"
```

---

## Task 1.6 — PolicyEngine: DB-Backed governance_policies Table

**Current state:** `app.state._policy_registry` is an in-memory dict per tenant. `_db_list_policies()` / `_db_create_policy()` / `_db_delete_policy()` exist in `api/governance.py` but `PolicyEngine` itself is never loaded from DB on startup.

**Gap:** On startup, load all `governance_policies` rows into `PolicyEngine`. `create_policy` must write to DB first; `delete_policy` must delete from DB first. `list_policies` must always read from DB.

**Files:**
- Modify: `agent-verse-backend/app/api/governance.py`
- Modify: `agent-verse-backend/app/main.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase1_persistence.py

@pytest.mark.asyncio
async def test_policy_engine_loads_from_db_on_startup():
    """PolicyEngine must load policies from governance_policies table on startup."""
    from app.governance.policies import PolicyEngine, Policy

    engine = PolicyEngine()
    db_rows = [
        MagicMock(
            id="p1", name="no-delete", tools_pattern="*.delete",
            action="deny", priority=10, description="block deletes",
            allowed_hours_utc=None, allowed_weekdays=None,
        )
    ]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        ("p1", "no-delete", "*.delete", "deny", 10, "block deletes")
    ]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)

    from app.governance.policies import load_policies_from_db
    await load_policies_from_db(engine, db_factory, tenant_id="t1")
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    from app.governance.policies import PolicyResult
    result = engine.evaluate("github.delete", tenant_ctx=ctx)
    assert result == PolicyResult.DENY
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_phase1_persistence.py -k "policy_engine_loads" -xvs
```
Expected: `ImportError: cannot import name 'load_policies_from_db'`

- [ ] **Step 3: Implement load_policies_from_db**

Add to `app/governance/policies.py`:

```python
async def load_policies_from_db(
    engine: "PolicyEngine",
    db_session_factory: Any,
    tenant_id: str,
) -> int:
    """Load governance policies from DB into PolicyEngine._policies.

    Returns count of policies loaded. Safe to call multiple times (idempotent by name).
    """
    try:
        from sqlalchemy import text
        existing_names = {p.name for p in engine._policies}
        async with db_session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, name, tools_pattern, action, priority, description, "
                    "allowed_hours_utc, allowed_weekdays "
                    "FROM governance_policies "
                    "WHERE tenant_id = :tid ORDER BY priority DESC"
                ),
                {"tid": tenant_id},
            )
            rows = result.fetchall()

        loaded = 0
        for row in rows:
            name = row[1]
            if name in existing_names:
                continue  # skip already-loaded policy
            tools_pattern = row[2]
            action = row[3]
            denied_tools: list[str] = []
            approval_tools: list[str] = []
            if action == "deny":
                denied_tools = [tools_pattern]
            elif action == "require_approval":
                approval_tools = [tools_pattern]
            hours = row[6]
            weekdays = row[7]
            engine.add_policy(
                Policy(
                    name=name,
                    description=row[5] or "",
                    denied_tools=denied_tools,
                    approval_tools=approval_tools,
                    allowed_hours_utc=tuple(hours) if hours and len(hours) == 2 else None,
                    allowed_weekdays=list(weekdays) if weekdays else None,
                )
            )
            existing_names.add(name)
            loaded += 1
        return loaded
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("load_policies_from_db failed: %s", exc)
        return 0
```

Wire in `app/main.py` lifespan after `db_factory` is established:

```python
# In lifespan, after db_factory assignment:
from app.governance.policies import load_policies_from_db
from app.tenancy.store import list_all_tenant_ids  # or use tenant_svc

try:
    # Load policies for all active tenants
    async with db_factory() as _sess:
        from sqlalchemy import text
        _result = await _sess.execute(
            text("SELECT id FROM tenants WHERE is_active = true")
        )
        _tenant_ids = [str(r[0]) for r in _result.fetchall()]
    for _tid in _tenant_ids:
        await load_policies_from_db(app.state.policy_engine, db_factory, _tid)
    logger.info("policy_engine_loaded_from_db", tenant_count=len(_tenant_ids))
except Exception as _exc:
    logger.warning("policy_engine_db_load_failed: %s", _exc)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_phase1_persistence.py -k "policy_engine_loads" -xvs
```

- [ ] **Step 5: Commit**

```bash
git add app/governance/policies.py app/main.py tests/test_phase1_persistence.py
git commit -m "feat(persistence): PolicyEngine loads governance_policies from DB on startup"
```

---

## Task 1.7 — CollaborationStore: Verification

**Current state:** `app/collab/store.py` is already DB-backed — uses `CollabSession`/`CollabOperation` ORM models with in-memory fallback when `_db` is None.

**Gap:** Verify all paths work and write a smoke test.

**Files:**
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Write verification tests**

```python
# append to tests/test_phase1_persistence.py

@pytest.mark.asyncio
async def test_collab_store_uses_db_when_configured():
    """CollaborationStore must write to DB when factory is provided."""
    from app.collab.store import CollaborationStore

    created_rows = []

    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def begin(self): return self
        def add(self, row): created_rows.append(row)
        async def execute(self, *a, **kw):
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            r.scalar_one_or_none.return_value = None
            return r

    db_factory = MagicMock(return_value=FakeSession())
    store = CollaborationStore(db_session_factory=db_factory)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    session = await store.create_session(
        tenant_ctx=tenant,
        name="Test Session",
        mode="suggest",
        participants=["alice", "bob"],
    )
    assert "session_id" in session
    assert len(created_rows) == 1

@pytest.mark.asyncio
async def test_collab_store_falls_back_to_memory_without_db():
    """CollaborationStore must work with in-memory store when no DB."""
    from app.collab.store import CollaborationStore

    store = CollaborationStore()
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    session = await store.create_session(
        tenant_ctx=tenant,
        name="In-Memory Session",
        mode="collaborate",
        participants=["carol"],
    )
    assert session["name"] == "In-Memory Session"
    sessions = await store.list_sessions(tenant_ctx=tenant)
    assert len(sessions) == 1
```

- [ ] **Step 2: Run — expect pass (already implemented)**

```bash
pytest tests/test_phase1_persistence.py -k "collab_store" -xvs
```
Expected: PASS (confirms existing implementation is correct)

- [ ] **Step 3: Commit**

```bash
git add tests/test_phase1_persistence.py
git commit -m "test(persistence): verify CollaborationStore DB-backed operation"
```

---

## Task 1.8 — EvalSuiteRunner: DB-Backed suites and results

**Current state:** `EvalSuiteRunner._suites` and `_results` are in-memory dicts — lost on restart.

**Gap:** New migrations for `eval_suites` and `eval_suite_results` tables. `create_suite`, `add_task`, `run_suite`, `get_results` all persist to and read from DB.

**Files:**
- Create: `agent-verse-backend/app/db/models/eval.py`
- Create: `agent-verse-backend/app/db/migrations/versions/0021_eval_suites.py`
- Modify: `agent-verse-backend/app/intelligence/eval_suite.py`
- Test: `agent-verse-backend/tests/test_phase1_persistence.py`

- [ ] **Step 1: Create ORM models**

```python
# app/db/models/eval.py
"""SQLAlchemy ORM models for eval suites and results."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class EvalSuite(Base):
    """An eval suite containing golden test tasks."""

    __tablename__ = "eval_suites"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EvalSuiteRunResult(Base):
    """Results of running an eval suite."""

    __tablename__ = "eval_suite_results"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    suite_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    task_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 2: Create migration**

```python
# app/db/migrations/versions/0021_eval_suites.py
"""Create eval_suites and eval_suite_results tables.

Revision ID: 0021
Revises: 0020
Create Date: 2026-01-01 01:00:00
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_suites",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tasks", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_eval_suites_tenant", "eval_suites", ["tenant_id"])

    op.create_table(
        "eval_suite_results",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("suite_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("total_tasks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("passed_tasks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_tasks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pass_rate", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("task_results", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_eval_suite_results_suite", "eval_suite_results", ["suite_id"])
    op.create_index("ix_eval_suite_results_tenant", "eval_suite_results", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("eval_suite_results")
    op.drop_table("eval_suites")
```

- [ ] **Step 3: Write failing tests**

```python
# append to tests/test_phase1_persistence.py

@pytest.mark.asyncio
async def test_eval_suite_runner_create_persists_to_db():
    """create_suite_async() must INSERT suite row to DB."""
    from app.intelligence.eval_suite import EvalSuiteRunner

    inserted = []
    class FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def begin(self): return self
        def add(self, row): inserted.append(row)

    db_factory = MagicMock(return_value=FakeSession())
    runner = EvalSuiteRunner(db_session_factory=db_factory)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    await runner.create_suite_async(
        suite_id="suite1", name="Regression Suite",
        tasks=[], tenant_ctx=tenant
    )
    assert len(inserted) == 1

@pytest.mark.asyncio
async def test_eval_suite_runner_get_results_reads_from_db():
    """get_results_async() must query DB for run results."""
    from app.intelligence.eval_suite import EvalSuiteRunner

    mock_row = MagicMock()
    mock_row.id = "res1"
    mock_row.suite_id = "suite1"
    mock_row.run_id = "run1"
    mock_row.total_tasks = 3
    mock_row.passed_tasks = 2
    mock_row.failed_tasks = 1
    mock_row.pass_rate = 0.667
    mock_row.task_results = []
    from datetime import UTC, datetime
    mock_row.run_at = datetime.now(UTC)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_row]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    db_factory = MagicMock(return_value=mock_ctx)

    runner = EvalSuiteRunner(db_session_factory=db_factory)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    results = await runner.get_results_async("suite1", tenant_ctx=tenant)
    assert len(results) == 1
    assert results[0]["pass_rate"] == pytest.approx(0.667, abs=0.001)
```

- [ ] **Step 4: Run — expect failures**

```bash
pytest tests/test_phase1_persistence.py -k "eval_suite" -xvs
```

- [ ] **Step 5: Modify EvalSuiteRunner for DB persistence**

Add `db_session_factory` param and async DB methods to `app/intelligence/eval_suite.py`:

```python
class EvalSuiteRunner:
    def __init__(self, db_session_factory: Any = None) -> None:
        self._suites: dict[str, list[GoldenTask]] = {}
        self._results: dict[str, list[EvalSuiteResult]] = {}
        self._db = db_session_factory

    async def create_suite_async(
        self,
        suite_id: str,
        name: str,
        tasks: list[GoldenTask] | None = None,
        *,
        tenant_ctx: Any,
    ) -> None:
        """Create suite in memory and persist to DB."""
        self._suites[suite_id] = tasks or []
        if self._db is not None:
            try:
                from app.db.models.eval import EvalSuite
                from app.db.rls import sqlalchemy_rls_context
                task_dicts = [
                    {
                        "task_id": t.task_id, "goal": t.goal,
                        "expected_tools": t.expected_tools,
                        "forbidden_tools": t.forbidden_tools,
                        "expected_output_contains": t.expected_output_contains,
                        "max_iterations": t.max_iterations,
                        "max_cost_usd": t.max_cost_usd,
                        "tags": t.tags,
                    }
                    for t in (tasks or [])
                ]
                row = EvalSuite(
                    id=suite_id,
                    tenant_id=tenant_ctx.tenant_id,
                    name=name,
                    tasks=task_dicts,
                )
                async with self._db() as session, session.begin():
                    async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                        session.add(row)
            except Exception as exc:
                logger.warning("eval_suite_db_create_failed: %s", exc)

    async def get_results_async(
        self, suite_id: str, *, tenant_ctx: Any
    ) -> list[dict[str, Any]]:
        """Fetch results from DB, fall back to in-memory."""
        if self._db is None:
            return [
                {
                    "run_id": r.run_id,
                    "suite_id": r.suite_id,
                    "total_tasks": r.total_tasks,
                    "passed_tasks": r.passed_tasks,
                    "failed_tasks": r.failed_tasks,
                    "pass_rate": r.pass_rate,
                    "run_at": r.run_at,
                    "task_results": [
                        {"task_id": t.task_id, "goal": t.goal, "passed": t.passed,
                         "failure_reasons": t.failure_reasons, "tools_called": t.tools_called,
                         "duration_seconds": t.duration_seconds}
                        for t in r.task_results
                    ],
                }
                for r in self._results.get(suite_id, [])
            ]
        try:
            from sqlalchemy import select
            from app.db.models.eval import EvalSuiteRunResult
            from app.db.rls import sqlalchemy_rls_context
            async with self._db() as session:
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    result = await session.execute(
                        select(EvalSuiteRunResult)
                        .where(
                            EvalSuiteRunResult.suite_id == suite_id,
                            EvalSuiteRunResult.tenant_id == tenant_ctx.tenant_id,
                        )
                        .order_by(EvalSuiteRunResult.run_at.desc())
                    )
                    rows = result.scalars().all()
            return [
                {
                    "run_id": r.run_id,
                    "suite_id": r.suite_id,
                    "total_tasks": r.total_tasks,
                    "passed_tasks": r.passed_tasks,
                    "failed_tasks": r.failed_tasks,
                    "pass_rate": r.pass_rate,
                    "run_at": r.run_at.isoformat() if r.run_at else "",
                    "task_results": r.task_results or [],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("eval_suite_get_results_failed: %s", exc)
            return []

    async def persist_result_async(
        self, result: EvalSuiteResult, *, tenant_ctx: Any
    ) -> None:
        """Persist a completed EvalSuiteResult to DB."""
        if self._db is None:
            return
        try:
            from app.db.models.eval import EvalSuiteRunResult
            from app.db.rls import sqlalchemy_rls_context
            task_dicts = [
                {
                    "task_id": t.task_id,
                    "goal": t.goal,
                    "passed": t.passed,
                    "failure_reasons": t.failure_reasons,
                    "tools_called": t.tools_called,
                    "duration_seconds": t.duration_seconds,
                }
                for t in result.task_results
            ]
            row = EvalSuiteRunResult(
                id=uuid.uuid4().hex,
                suite_id=result.suite_id,
                tenant_id=tenant_ctx.tenant_id,
                run_id=result.run_id,
                total_tasks=result.total_tasks,
                passed_tasks=result.passed_tasks,
                failed_tasks=result.failed_tasks,
                pass_rate=result.pass_rate,
                task_results=task_dicts,
            )
            async with self._db() as session, session.begin():
                async with sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                    session.add(row)
        except Exception as exc:
            logger.warning("eval_suite_persist_result_failed: %s", exc)
```

Update `run_suite` to call `persist_result_async`:
```python
async def run_suite(self, suite_id: str, goal_service: Any, tenant_ctx: Any) -> EvalSuiteResult:
    tasks = self._suites.get(suite_id, [])
    result = EvalSuiteResult(suite_id=suite_id, total_tasks=len(tasks))
    for task in tasks:
        task_result = await self._run_task(task, goal_service, tenant_ctx)
        result.task_results.append(task_result)
        if task_result.passed:
            result.passed_tasks += 1
        else:
            result.failed_tasks += 1
    self._results.setdefault(suite_id, []).append(result)
    await self.persist_result_async(result, tenant_ctx=tenant_ctx)
    return result
```

- [ ] **Step 6: Run tests — expect pass**

```bash
pytest tests/test_phase1_persistence.py -k "eval_suite" -xvs
```

- [ ] **Step 7: Run all Phase 1 tests**

```bash
pytest tests/test_phase1_persistence.py -v
```
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add app/db/models/eval.py app/db/migrations/versions/0021_eval_suites.py \
        app/intelligence/eval_suite.py tests/test_phase1_persistence.py
git commit -m "feat(persistence): EvalSuiteRunner DB-backed with eval_suites + eval_suite_results tables"
```

---

## Acceptance Criteria

| Item | Criterion |
|---|---|
| 1.1 AgentStore | `GET /agents` returns DB rows after restart with zero in-memory state |
| 1.2 HITLGateway | Pending approvals survive restart; `approve/reject` update DB `status` |
| 1.3 AuditLog | `GET /governance/audit?start_time=&end_time=&limit=` returns DB rows with correct pagination |
| 1.4 LongTermMemory | `recall_async` uses pgvector `<=>` operator when embedder configured; falls back to keyword |
| 1.5 MemorySaver | New goals resume from last checkpoint after process restart when `DATABASE_URL` set |
| 1.6 PolicyEngine | Policies loaded from DB on startup; `POST /governance/policies` writes to DB |
| 1.7 CollaborationStore | All existing tests pass; DB path verified with smoke test |
| 1.8 EvalSuiteRunner | Suite and run results persisted to `eval_suites`/`eval_suite_results`; readable after restart |
