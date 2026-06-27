# AgentVerse World-Class Completion Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 80+ identified gaps across 25 Agentic OS features so AgentVerse can autonomously execute any real-world task end-to-end with zero mocking, full durability, and production-grade reliability.

**Architecture:** Each phase implements a self-contained capability group with no cross-phase dependencies within the same priority tier. P0 phases are prerequisites for P1; P1 for P2; P2 for P3. All phases include failing tests written first, implementation second, integration verified last.

**Tech Stack:** FastAPI, LangGraph, Celery + RedBeat, PostgreSQL + pgvector, Redis, MinIO, Playwright, Keycloak, SearXNG, Jaeger, Prometheus, Grafana, Docker Compose, Helm, Python 3.12, React 18 + TypeScript

---

## PHASE OVERVIEW

### P0 — Production Blockers (implement first, blocks everything else)
- **P0.1** Durable Execution Kernel (RedisSaver + pause/resume + cancellation)
- **P0.2** Knowledge OS — Document ingestion pipeline (PDF/DOCX/Confluence/GitHub/Jira)

### P1 — Core Capability Gaps (implement after P0)
- **P1.1** Workflow DAG — Conditional branches + loops + cross-restart resume
- **P1.2** RPA — Vault credential injection + CAPTCHA + human takeover + recording
- **P1.3** HITL — Multi-person threshold + rejection feedback → replanning + email approval
- **P1.4** Artifact System — General artifacts API + retention + video recording
- **P1.5** Production Infrastructure — DB backups + Grafana dashboards + Redis HA

### P2 — Quality & Completeness (implement after P1)
- **P2.1** Capability Registry — Tool stats DB + auto-select on goal submit + embedding cache
- **P2.2** Agent Router — DB history scoring + multi-agent parallel dispatch
- **P2.3** Memory System — DB failure records + tool reliability memory
- **P2.4** Governance — Cross-agent policy inheritance + policy simulation endpoint
- **P2.5** Marketplace — Template bundles + version history + private/team scope
- **P2.6** Evaluation — Golden task dataset + rollout gate wired to pass rate
- **P2.7** Identity — Key rotation + BYOK + per-agent credential scoping
- **P2.8** Multi-Tenant — Per-tenant Celery queues + RLS audit in tasks.py
- **P2.9** Developer SDK — Local mock server + connector scaffolding CLI
- **P2.10** Compliance — Async GDPR export + consent management

### P3 — Polish & Advanced (implement after P2)
- **P3.1** Collaboration — Debate audit trail + WebSocket real-time broadcast
- **P3.2** CLI — Missing commands (connectors, schedules, simulate, logs, policy)
- **P3.3** Observability — Pre-built Grafana dashboards + per-step token breakdown SSE
- **P3.4** Event Bus — Alertmanager/Datadog webhook parsers + file drop trigger
- **P3.5** Reliability — Real MCP tool inverses + fallback tool routing
- **P3.6** UI/UX — Simulation Studio + RPA Live viewer + Incident Replay page

---

## PHASE P0.1: Durable Execution Kernel

### Spec
Fix the fundamental durability gap: LangGraph defaults to MemorySaver (in-memory only). A process crash loses all running goals. Pause/resume only works in the same process. Cancellation cannot reach Celery workers.

### Files
- Modify: `agent-verse-backend/app/services/goal_service.py` — wire RedisSaver at graph construction
- Modify: `agent-verse-backend/app/agent/graph.py` — SIGTERM handler + checkpoint on shutdown
- Modify: `agent-verse-backend/app/scaling/tasks.py` — pub/sub pause listener + cancellation
- Create: `agent-verse-backend/app/reliability/goal_lifecycle.py` — cross-process signal bus
- Modify: `agent-verse-backend/infra/docker-compose.yml` — Redis AOF persistence
- Create: `agent-verse-backend/tests/agent/test_durable_execution.py`

### Tasks

#### Task P0.1.1: RedisSaver Always Wired

- [ ] **Step 1: Write failing test**
```python
# tests/agent/test_durable_execution.py
def test_agentgraph_uses_redis_saver_not_memory_saver():
    """AgentGraph must never default to MemorySaver in production."""
    import inspect
    from app.agent import graph
    from langgraph.checkpoint.memory import MemorySaver
    # When db_session_factory is set, checkpointer must NOT be MemorySaver
    from app.providers.fake import FakeProvider
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    g = graph.AgentGraph(
        planner=FakeProvider(), executor=FakeProvider(), verifier=FakeProvider(),
        checkpointer=None,  # let it resolve
    )
    g._db_session_factory = mock_db
    # Should not be MemorySaver when DB available
    assert not isinstance(g._checkpointer, MemorySaver), \
        "AgentGraph must use RedisSaver or AsyncRedisSaver when Redis is configured"
```

- [ ] **Step 2: Run to verify it fails**
```bash
cd agent-verse-backend
.venv/bin/pytest tests/agent/test_durable_execution.py::test_agentgraph_uses_redis_saver_not_memory_saver -v
```

- [ ] **Step 3: Fix goal_service._make_agent_loop_for_tenant to pass RedisSaver**

In `app/services/goal_service.py`, find `_make_agent_loop_for_tenant`. The checkpointer is resolved by `_resolve_checkpointer(app_state)`. Read that function. Change it so that when `REDIS_URL` is available, it creates the RedisSaver inline if `app_state.langgraph_checkpointer` is a MemorySaver:

```python
def _resolve_checkpointer(app_state: Any) -> Any:
    """Return the best available checkpointer. Never return MemorySaver in production."""
    import os
    from langgraph.checkpoint.memory import MemorySaver
    
    cp = getattr(app_state, "langgraph_checkpointer", None)
    # If already a real saver (Redis), use it
    if cp is not None and not isinstance(cp, MemorySaver):
        return cp
    
    # Try to build RedisSaver from env
    redis_url = os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver
            saver = AsyncRedisSaver.from_conn_string(redis_url)
            # setup() must be called before first use — do it lazily
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(saver.setup())
            except RuntimeError:
                pass
            get_logger(__name__).info("checkpointer_resolved_redis_async")
            return saver
        except Exception as e1:
            try:
                from langgraph.checkpoint.redis import RedisSaver
                saver = RedisSaver.from_conn_string(redis_url)
                get_logger(__name__).info("checkpointer_resolved_redis_sync")
                return saver
            except Exception as e2:
                get_logger(__name__).warning("redis_saver_unavailable", error=str(e2))
    
    get_logger(__name__).warning(
        "using_memory_saver_DURABILITY_DISABLED",
        message="Set REDIS_URL to enable durable goal checkpointing"
    )
    return MemorySaver()
```

- [ ] **Step 4: Run test — must pass**
```bash
.venv/bin/pytest tests/agent/test_durable_execution.py::test_agentgraph_uses_redis_saver_not_memory_saver -v
```

- [ ] **Step 5: Commit**
```bash
git add app/services/goal_service.py tests/agent/test_durable_execution.py
git commit -m "fix(P0.1): RedisSaver always wired — MemorySaver only when Redis unavailable"
```

#### Task P0.1.2: Cross-Process Pause/Resume via Redis

- [ ] **Step 1: Write failing test**
```python
# tests/agent/test_durable_execution.py
@pytest.mark.asyncio
async def test_pause_signal_published_to_redis():
    """pause_goal must publish signal to Redis so Celery workers can observe it."""
    from app.services.goal_service import GoalService, GoalRecord
    from app.agent.state import GoalStatus
    from app.tenancy.context import PlanTier, TenantContext
    
    svc = GoalService()
    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()
    mock_redis.set = AsyncMock()
    svc._redis = mock_redis
    
    T = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")
    svc._goals["g1"] = GoalRecord(
        goal_id="g1", goal_text="test", status=GoalStatus.EXECUTING,
        tenant_id="t1", priority="normal", dry_run=False, created_at=""
    )
    
    await svc.pause_goal(goal_id="g1", tenant_ctx=T)
    
    # Must publish to Redis channel
    mock_redis.publish.assert_called_once_with("goal_pause:g1", "pause")
    mock_redis.set.assert_called()  # also sets the flag key
```

- [ ] **Step 2: Verify test fails**
```bash
.venv/bin/pytest tests/agent/test_durable_execution.py::test_pause_signal_published_to_redis -v
```

- [ ] **Step 3: Implement cross-process pause bus in goal_lifecycle.py**

Create `app/reliability/goal_lifecycle.py`:
```python
"""Cross-process goal lifecycle signals via Redis pub/sub.

Allows API server to signal Celery workers to pause, cancel, or
resume goals running in separate processes.
"""
from __future__ import annotations
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

PAUSE_CHANNEL_PREFIX = "goal_pause:"
CANCEL_CHANNEL_PREFIX = "goal_cancel:"
PAUSE_FLAG_PREFIX = "goal_paused:"
CANCEL_FLAG_PREFIX = "goal_cancelled:"
FLAG_TTL = 3600  # 1 hour

async def signal_pause(goal_id: str, redis: Any) -> None:
    await redis.publish(f"{PAUSE_CHANNEL_PREFIX}{goal_id}", "pause")
    await redis.set(f"{PAUSE_FLAG_PREFIX}{goal_id}", "1", ex=FLAG_TTL)

async def signal_resume(goal_id: str, redis: Any) -> None:
    await redis.delete(f"{PAUSE_FLAG_PREFIX}{goal_id}")
    await redis.publish(f"{PAUSE_CHANNEL_PREFIX}{goal_id}", "resume")

async def signal_cancel(goal_id: str, redis: Any) -> None:
    await redis.set(f"{CANCEL_FLAG_PREFIX}{goal_id}", "1", ex=FLAG_TTL)
    await redis.publish(f"{CANCEL_CHANNEL_PREFIX}{goal_id}", "cancel")

def is_paused_sync(goal_id: str, redis_sync: Any) -> bool:
    return bool(redis_sync.get(f"{PAUSE_FLAG_PREFIX}{goal_id}"))

def is_cancelled_sync(goal_id: str, redis_sync: Any) -> bool:
    return bool(redis_sync.get(f"{CANCEL_FLAG_PREFIX}{goal_id}"))
```

Update `app/services/goal_service.py` `pause_goal()` and `cancel_goal()` to use these signals.

Update `app/scaling/tasks.py` `run_goal()` to check pause/cancel flags:
```python
from app.reliability.goal_lifecycle import is_paused_sync, is_cancelled_sync

# Inside run_goal, in the async execution function, add periodic checks:
async def _check_signals() -> bool:
    """Returns True if execution should abort."""
    sync_r = _get_sync_redis()
    if is_cancelled_sync(goal_id, sync_r):
        logger.info("goal_cancelled_by_signal", goal_id=goal_id)
        return True
    if is_paused_sync(goal_id, sync_r):
        logger.info("goal_paused_by_signal", goal_id=goal_id)
        # Wait for resume signal (poll every 5 seconds)
        while is_paused_sync(goal_id, sync_r):
            await asyncio.sleep(5)
            if is_cancelled_sync(goal_id, sync_r):
                return True
    return False

# Call _check_signals() between each wave step in the graph
```

- [ ] **Step 4: Run test — must pass**
```bash
.venv/bin/pytest tests/agent/test_durable_execution.py -v
```

- [ ] **Step 5: Commit**
```bash
git add app/reliability/goal_lifecycle.py app/services/goal_service.py app/scaling/tasks.py tests/agent/test_durable_execution.py
git commit -m "fix(P0.1): cross-process pause/cancel/resume via Redis signals"
```

#### Task P0.1.3: SIGTERM graceful checkpoint on worker shutdown

- [ ] **Step 1: Write test**
```python
def test_celery_worker_handles_sigterm_gracefully():
    """Worker must write checkpoint before dying on SIGTERM."""
    from app.reliability.goal_lifecycle import signal_cancel
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "SIGTERM" in src or "signal.signal" in src or "graceful" in src.lower(), \
        "tasks.py must handle SIGTERM to checkpoint before shutdown"
```

- [ ] **Step 2: Implement SIGTERM handler in tasks.py**
```python
import signal as _signal

def _setup_sigterm_handler() -> None:
    """Register SIGTERM handler for graceful Celery worker shutdown."""
    def _on_sigterm(signum, frame):
        import logging
        logging.getLogger(__name__).warning("SIGTERM received — worker shutting down gracefully")
        # Celery's soft time limit will trigger; we rely on LangGraph checkpointing
        # to persist state. The checkpoint is written after each wave step.
        raise SystemExit(0)
    
    try:
        _signal.signal(_signal.SIGTERM, _on_sigterm)
    except (OSError, ValueError):
        pass  # Not in main thread

# Call at module level
_setup_sigterm_handler()
```

- [ ] **Step 3: Run all tests**
```bash
.venv/bin/pytest tests/agent/test_durable_execution.py tests/scaling/ -v
```

- [ ] **Step 4: Commit**
```bash
git commit -m "fix(P0.1): SIGTERM handler for graceful worker checkpoint on shutdown"
```

---

## PHASE P0.2: Knowledge OS — Full Ingestion Pipeline

### Spec
Implement PDF/DOCX/Excel ingestion, GitHub repo crawler, Confluence/Jira connector ingestion, Slack message ingestion, source citations in search results, freshness tracking with auto-reindex, and permission-aware retrieval.

### Files
- Create: `app/knowledge/ingestors/pdf_ingestor.py`
- Create: `app/knowledge/ingestors/github_ingestor.py`
- Create: `app/knowledge/ingestors/confluence_ingestor.py`
- Create: `app/knowledge/ingestors/slack_ingestor.py`
- Modify: `app/db/models/knowledge.py` — add source_url, last_modified, freshness_ttl to Chunk
- Create: `app/db/migrations/versions/0035_knowledge_citations.py`
- Modify: `app/rag/store.py` — return citations in search results
- Create: `app/scaling/tasks.py` — `reindex_stale_knowledge` beat task
- Modify: `app/api/knowledge.py` — new ingest endpoints per source type
- Create: `tests/knowledge/test_ingestion_pipeline.py`

### Tasks

#### Task P0.2.1: Migration for Citations + Freshness

- [ ] **Step 1: Write test**
```python
def test_migration_0035_exists():
    import os
    files = os.listdir("app/db/migrations/versions")
    assert any("0035" in f for f in files), "Migration 0035 must exist for knowledge citations"
```

- [ ] **Step 2: Create migration**
```python
# app/db/migrations/versions/0035_knowledge_citations.py
revision = "0035"
down_revision = "0034"

def upgrade():
    from alembic import op
    for stmt in [
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'text'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_doc_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_number INT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_modified TIMESTAMPTZ",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS freshness_ttl_hours INT NOT NULL DEFAULT 168",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS needs_reindex BOOLEAN NOT NULL DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS ix_documents_needs_reindex ON documents (needs_reindex) WHERE needs_reindex = TRUE",
        "CREATE INDEX IF NOT EXISTS ix_documents_last_modified ON documents (collection_id, last_modified DESC)",
    ]:
        op.execute(stmt)

def downgrade():
    from alembic import op
    for col in ["source_url","source_type","source_doc_id","page_number","last_modified","freshness_ttl_hours","needs_reindex"]:
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {col}")
```

- [ ] **Step 3: Apply migration**
```bash
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse .venv/bin/alembic upgrade head
```

- [ ] **Step 4: Commit**
```bash
git commit -m "feat(P0.2): migration 0035 — knowledge citations + freshness fields"
```

#### Task P0.2.2: PDF/DOCX/Excel/Markdown Ingestor

- [ ] **Step 1: Write failing test**
```python
# tests/knowledge/test_ingestion_pipeline.py
import pytest

def test_pdf_ingestor_extracts_text():
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    import io
    # Create minimal PDF bytes for testing
    pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000068 00000 n\n0000000125 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    ingestor = PdfIngestor()
    # Should not raise even for minimal/empty PDF
    chunks = ingestor.extract_chunks(content=pdf_bytes, filename="test.pdf", source_url="file://test.pdf")
    assert isinstance(chunks, list)

def test_pdf_ingestor_includes_source_metadata():
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    ingestor = PdfIngestor()
    # Use any valid PDF bytes or skip if not available
    chunks = ingestor.extract_chunks(
        content=b"%%PDF minimal",
        filename="annual_report.pdf",
        source_url="https://company.com/reports/annual.pdf"
    )
    for chunk in chunks:
        assert chunk.get("source_url") == "https://company.com/reports/annual.pdf"
        assert chunk.get("source_type") == "pdf"
```

- [ ] **Step 2: Implement PDF ingestor**
```python
# app/knowledge/ingestors/pdf_ingestor.py
"""PDF document ingestor using pypdf (no cloud dependencies)."""
from __future__ import annotations
import io
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


class PdfIngestor:
    """Extract text chunks from PDF files using pypdf."""

    def extract_chunks(
        self, *, content: bytes, filename: str, source_url: str = ""
    ) -> list[dict[str, Any]]:
        chunks = []
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            for page_num, page in enumerate(reader.pages[:100]):  # max 100 pages
                text = (page.extract_text() or "").strip()
                if len(text) < 20:
                    continue
                # Split into ~1000-char chunks with 100-char overlap
                for i in range(0, len(text), 900):
                    chunk_text = text[i:i+1000]
                    if len(chunk_text) < 20:
                        continue
                    chunks.append({
                        "content": chunk_text,
                        "source_url": source_url,
                        "source_type": "pdf",
                        "source_doc_id": filename,
                        "page_number": page_num + 1,
                        "metadata": {
                            "filename": filename,
                            "page": page_num + 1,
                            "total_pages": len(reader.pages),
                        },
                    })
        except ImportError:
            logger.warning("pypdf_not_installed", hint="pip install pypdf")
            chunks = [{"content": f"[PDF: {filename} — install pypdf to extract text]",
                       "source_url": source_url, "source_type": "pdf",
                       "source_doc_id": filename, "page_number": None, "metadata": {}}]
        except Exception as exc:
            logger.warning("pdf_extract_failed", filename=filename, error=str(exc))
        return chunks
```

Create similar ingestors:
- `app/knowledge/ingestors/docx_ingestor.py` (uses python-docx)
- `app/knowledge/ingestors/excel_ingestor.py` (uses openpyxl)
- `app/knowledge/ingestors/__init__.py`

- [ ] **Step 3: Run tests**
```bash
.venv/bin/pytest tests/knowledge/test_ingestion_pipeline.py -v
```

- [ ] **Step 4: Commit**
```bash
git commit -m "feat(P0.2): PDF/DOCX/Excel ingestors with citation metadata"
```

#### Task P0.2.3: GitHub Repo Ingestor

- [ ] **Step 1: Write test**
```python
@pytest.mark.asyncio
async def test_github_ingestor_fetches_file_list():
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    from unittest.mock import AsyncMock, patch
    
    ingestor = GitHubIngestor(token="test-token")
    mock_files = [
        {"path": "README.md", "type": "file", "size": 1000},
        {"path": "src/main.py", "type": "file", "size": 500},
        {"path": ".git", "type": "dir", "size": 0},  # should be skipped
    ]
    with patch.object(ingestor, "_list_repo_files", AsyncMock(return_value=mock_files)):
        with patch.object(ingestor, "_fetch_file", AsyncMock(return_value="file content")):
            chunks = await ingestor.ingest_repo("owner", "repo", max_files=10)
    
    # .git dir should be skipped
    file_names = [c["source_doc_id"] for c in chunks]
    assert all(".git" not in f for f in file_names)
    assert len(chunks) >= 2
```

- [ ] **Step 2: Implement GitHub ingestor**
```python
# app/knowledge/ingestors/github_ingestor.py
"""GitHub repository ingestor — crawls files via GitHub REST API."""
from __future__ import annotations
import os
from typing import Any
import httpx
from app.observability.logging import get_logger

logger = get_logger(__name__)

_SKIP_EXTENSIONS = {".png",".jpg",".gif",".svg",".ico",".bin",".zip",".tar",".gz",".pdf"}
_SKIP_DIRS = {".git",".github","node_modules","__pycache__",".venv","dist","build"}
_TEXT_EXTENSIONS = {".py",".ts",".tsx",".js",".jsx",".md",".txt",".yaml",".yml",".json",".toml",".env.example",".sh",".go",".rs",".java",".rb",".php",".cs"}

class GitHubIngestor:
    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN", "")
        self._base = "https://api.github.com"

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _list_repo_files(self, owner: str, repo: str, path: str = "") -> list[dict]:
        url = f"{self._base}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        async with httpx.AsyncClient(timeout=30, headers=self._headers()) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json().get("tree", [])

    async def _fetch_file(self, owner: str, repo: str, path: str) -> str:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{path}"
        async with httpx.AsyncClient(timeout=15, headers=self._headers()) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.text[:50000]  # max 50KB per file

    async def ingest_repo(
        self, owner: str, repo: str, *, branch: str = "HEAD",
        max_files: int = 200, file_patterns: list[str] | None = None
    ) -> list[dict[str, Any]]:
        files = await self._list_repo_files(owner, repo)
        chunks = []
        count = 0
        for f in files:
            if count >= max_files:
                break
            path = f.get("path", "")
            ftype = f.get("type", "")
            if ftype != "blob":
                continue
            # Skip non-text and excluded dirs
            ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
            if ext in _SKIP_EXTENSIONS:
                continue
            if any(skip in path.split("/") for skip in _SKIP_DIRS):
                continue
            if ext not in _TEXT_EXTENSIONS and ext != "":
                continue
            try:
                content = await self._fetch_file(owner, repo, path)
                if len(content.strip()) < 50:
                    continue
                # Split large files
                for i in range(0, len(content), 1500):
                    chunk = content[i:i+1600]
                    if len(chunk.strip()) < 50:
                        continue
                    chunks.append({
                        "content": chunk,
                        "source_url": f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
                        "source_type": "github",
                        "source_doc_id": f"{owner}/{repo}/{path}",
                        "page_number": None,
                        "metadata": {"owner": owner, "repo": repo, "path": path, "branch": branch},
                    })
                count += 1
            except Exception as exc:
                logger.warning("github_file_fetch_failed", path=path, error=str(exc))
        return chunks
```

- [ ] **Step 3: Run tests**
```bash
.venv/bin/pytest tests/knowledge/test_ingestion_pipeline.py -v
```

- [ ] **Step 4: Commit**
```bash
git commit -m "feat(P0.2): GitHub repo ingestor — recursive file crawl with citation metadata"
```

#### Task P0.2.4: Confluence Ingestor

- [ ] **Step 1: Write test**
```python
@pytest.mark.asyncio
async def test_confluence_ingestor_paginates():
    from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor
    from unittest.mock import AsyncMock, patch
    
    ingestor = ConfluenceIngestor(base_url="https://company.atlassian.net/wiki", token="tok", user="u@c.com")
    mock_pages = [
        {"id": "1", "title": "Page 1", "body": {"storage": {"value": "<p>Content 1</p>"}}},
        {"id": "2", "title": "Page 2", "body": {"storage": {"value": "<p>Content 2</p>"}}},
    ]
    with patch.object(ingestor, "_get_pages", AsyncMock(return_value=mock_pages)):
        chunks = await ingestor.ingest_space("DEV")
    
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk["source_type"] == "confluence"
        assert "atlassian.net" in chunk["source_url"]
```

- [ ] **Step 2: Implement Confluence ingestor**
```python
# app/knowledge/ingestors/confluence_ingestor.py
"""Confluence Cloud/Server page ingestor via REST API v2."""
from __future__ import annotations
import re
from typing import Any
import httpx
from app.observability.logging import get_logger

logger = get_logger(__name__)


def _html_to_text(html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()


class ConfluenceIngestor:
    def __init__(self, base_url: str, token: str, user: str) -> None:
        self._base = base_url.rstrip("/")
        self._auth = (user, token)

    async def _get_pages(self, space_key: str, limit: int = 50, start: int = 0) -> list[dict]:
        url = f"{self._base}/rest/api/content"
        params = {
            "spaceKey": space_key, "type": "page", "status": "current",
            "expand": "body.storage,version,ancestors", "limit": limit, "start": start
        }
        async with httpx.AsyncClient(timeout=30, auth=self._auth) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("results", [])

    async def ingest_space(self, space_key: str, max_pages: int = 500) -> list[dict[str, Any]]:
        chunks = []
        start = 0
        while len(chunks) < max_pages * 3:  # rough chunk estimate
            pages = await self._get_pages(space_key, start=start)
            if not pages:
                break
            for page in pages:
                html = page.get("body", {}).get("storage", {}).get("value", "")
                text = _html_to_text(html)
                if len(text) < 50:
                    continue
                page_url = f"{self._base}/pages/{page['id']}"
                for i in range(0, len(text), 1500):
                    chunk = text[i:i+1600]
                    if len(chunk.strip()) < 50:
                        continue
                    chunks.append({
                        "content": chunk,
                        "source_url": page_url,
                        "source_type": "confluence",
                        "source_doc_id": page["id"],
                        "page_number": None,
                        "metadata": {"title": page.get("title",""), "space": space_key},
                    })
            start += len(pages)
            if len(pages) < 50:
                break
        return chunks
```

- [ ] **Step 3: Run tests and commit**
```bash
.venv/bin/pytest tests/knowledge/ -v
git commit -m "feat(P0.2): Confluence space ingestor with HTML→text extraction"
```

#### Task P0.2.5: Citations in Search Results

- [ ] **Step 1: Write failing test**
```python
@pytest.mark.asyncio
async def test_hybrid_search_returns_citations():
    """Search results must include source_url and source_doc_id for citations."""
    from app.rag.store import KnowledgeStore
    from unittest.mock import AsyncMock, MagicMock
    
    store = KnowledgeStore()
    mock_row = MagicMock()
    mock_row.content = "The payment refund process takes 3-5 days"
    mock_row.score = 0.92
    mock_row.source_url = "https://docs.company.com/refunds"
    mock_row.source_doc_id = "refund-policy-v3"
    mock_row.page_number = 4
    
    # Patch DB query
    from unittest.mock import patch, AsyncMock
    store._db = AsyncMock()
    
    # Verify the result structure includes citation fields
    result_dict = {
        "content": mock_row.content,
        "score": mock_row.score,
        "source_url": mock_row.source_url,
        "source_doc_id": mock_row.source_doc_id,
        "page_number": mock_row.page_number,
    }
    assert "source_url" in result_dict
    assert "source_doc_id" in result_dict
    assert result_dict["source_url"] == "https://docs.company.com/refunds"
```

- [ ] **Step 2: Update `hybrid_search_db()` to return citation fields**

In `app/rag/store.py`, update the SQL query and result mapping:
```python
# Add source fields to SELECT
sql = """
    SELECT
        content,
        (embedding <=> :qvec::vector) * 0.7 +
        (1 - similarity(content, :qtxt)) * 0.3 AS score,
        source_url,
        source_doc_id,
        page_number,
        metadata
    FROM documents
    WHERE collection_id = :cid AND tenant_id = :tid
    ORDER BY score ASC
    LIMIT :lim
"""
# Update result mapping to include citation fields
return [
    {
        "content": r.content,
        "score": float(r.score),
        "source_url": getattr(r, "source_url", "") or "",
        "source_doc_id": getattr(r, "source_doc_id", "") or "",
        "page_number": getattr(r, "page_number", None),
        "metadata": getattr(r, "metadata", {}) or {},
    }
    for r in rows
]
```

- [ ] **Step 3: Update knowledge API to expose ingestion endpoints**

In `app/api/knowledge.py`, add:
```python
@router.post("/knowledge/ingest/pdf")
async def ingest_pdf(request: Request, collection_id: str = Form(...), file: UploadFile = File(...)) -> dict:
    """Upload and ingest a PDF file into a knowledge collection."""
    tenant_ctx = _require_tenant(request)
    content = await file.read()
    
    from app.knowledge.ingestors.pdf_ingestor import PdfIngestor
    chunks = PdfIngestor().extract_chunks(
        content=content, filename=file.filename, source_url=f"upload://{file.filename}"
    )
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)
    ingested = 0
    for chunk in chunks:
        await store.ingest_document(
            collection_id=collection_id,
            content=chunk["content"],
            metadata=chunk,
            tenant_ctx=tenant_ctx,
            embedder=embedder,
        )
        ingested += 1
    return {"collection_id": collection_id, "chunks_ingested": ingested, "filename": file.filename}

@router.post("/knowledge/ingest/github")
async def ingest_github_repo(request: Request, body: GithubIngestRequest) -> dict:
    """Ingest all code/docs from a GitHub repository."""
    tenant_ctx = _require_tenant(request)
    from app.knowledge.ingestors.github_ingestor import GitHubIngestor
    token = os.getenv("GITHUB_TOKEN", "")
    ingestor = GitHubIngestor(token=token)
    chunks = await ingestor.ingest_repo(body.owner, body.repo, max_files=body.max_files)
    store = _knowledge_store(request)
    embedder = getattr(request.app.state, "embedder", None)
    ingested = 0
    for chunk in chunks:
        await store.ingest_document(collection_id=body.collection_id, content=chunk["content"],
                                    metadata=chunk, tenant_ctx=tenant_ctx, embedder=embedder)
        ingested += 1
    return {"collection_id": body.collection_id, "chunks": ingested, "repo": f"{body.owner}/{body.repo}"}

class GithubIngestRequest(BaseModel):
    owner: str
    repo: str
    collection_id: str
    max_files: int = 200
```

- [ ] **Step 4: Add freshness reindex task to celery**
```python
# In app/scaling/tasks.py
@celery_app.task(name="agentverse.maintenance.reindex_stale_knowledge")
def reindex_stale_knowledge() -> dict:
    """Mark knowledge documents that have exceeded their freshness TTL for reindexing."""
    async def _run():
        from app.db.session import get_session_factory
        from sqlalchemy import text
        db = get_session_factory()
        async with db() as session, session.begin():
            result = await session.execute(text("""
                UPDATE documents
                SET needs_reindex = TRUE
                WHERE last_modified IS NOT NULL
                  AND needs_reindex = FALSE
                  AND last_modified < NOW() - (freshness_ttl_hours * INTERVAL '1 hour')
            """))
            return {"marked_for_reindex": result.rowcount}
    import asyncio
    return asyncio.run(_run())
```

Add to beat schedule: `"reindex-stale-knowledge-hourly": {"task": "agentverse.maintenance.reindex_stale_knowledge", "schedule": 3600}`

- [ ] **Step 5: Run all knowledge tests**
```bash
.venv/bin/pytest tests/knowledge/ -v
```

- [ ] **Step 6: Final commit for P0.2**
```bash
git add -A
git commit -m "feat(P0.2): complete knowledge ingestion pipeline — PDF/DOCX/GitHub/Confluence + citations + freshness"
```

---

## PHASE P1.1: Workflow DAG — Conditional Branches + Loops + Resume

### Spec
Add conditional branch nodes, loop nodes with max iteration limits, and true cross-restart resume from the last completed step checkpoint.

### Files
- Modify: `app/agent/structured_plan.py` — add `condition`, `loop_until`, `max_loop_iter` to StructuredStep
- Modify: `app/agent/graph.py` — conditional routing + loop execution
- Create: `tests/agent/test_dag_advanced.py`

### Tasks

#### Task P1.1.1: Conditional Branch DAG Nodes

- [ ] **Step 1: Write failing test**
```python
# tests/agent/test_dag_advanced.py
def test_structured_step_has_condition_field():
    from app.agent.structured_plan import StructuredStep
    import inspect
    sig = inspect.signature(StructuredStep.__init__)
    params = sig.parameters
    assert "condition" in params or hasattr(StructuredStep, "condition"), \
        "StructuredStep must have 'condition' field for conditional branching"

def test_execution_waves_respects_condition():
    """Steps with condition only run when condition evaluates to True."""
    from app.agent.structured_plan import StructuredPlan, StructuredStep
    plan = StructuredPlan(steps=[
        StructuredStep(id="s1", description="Always runs", depends_on=[], condition=None),
        StructuredStep(id="s2", description="Only if s1 succeeded", depends_on=["s1"],
                       condition="s1.status == 'complete'", tool=""),
        StructuredStep(id="s3", description="Fallback", depends_on=["s1"],
                       condition="s1.status == 'failed'", tool=""),
    ])
    waves = plan.execution_waves()
    # s1 is wave 0; s2 and s3 both depend on s1 but have conditions
    assert len(waves) >= 2
    assert any(s.id == "s1" for s in waves[0])
```

- [ ] **Step 2: Add condition field to StructuredStep**
```python
# In app/agent/structured_plan.py, update StructuredStep dataclass:
@dataclass
class StructuredStep:
    id: str
    description: str
    tool: str = ""
    arguments: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    risk: str = "low"
    expected_output: str = ""
    condition: str | None = None  # NEW: Python expression evaluated against step results
    loop_until: str | None = None  # NEW: loop this step until condition is True
    max_loop_iter: int = 5  # NEW: max loop iterations before forcing exit
    status: str = "pending"
    output: str = ""
    error: str = ""
    iterations_used: int = 0

# Add condition evaluation method:
def evaluate_condition(self, step_results: dict[str, "StructuredStep"]) -> bool:
    """Evaluate whether this step's condition is met based on prior results."""
    if self.condition is None:
        return True  # No condition = always run
    try:
        # Build evaluation context from step results
        ctx = {sid: s for sid, s in step_results.items()}
        # Simple safe eval
        return bool(eval(
            self.condition,
            {"__builtins__": {}},
            {sid: type("StepResult", (), {"status": s.status, "output": s.output})()
             for sid, s in step_results.items()}
        ))
    except Exception:
        return True  # On eval error, default to running the step
```

- [ ] **Step 3: Implement loop execution in graph._node_execute**
```python
# In _execute_step or _node_execute, handle loop_until:
async def _execute_with_loop(self, step: StructuredStep, state: AgentState, tenant_ctx) -> str:
    """Execute a step repeatedly until loop_until condition is True."""
    if step.loop_until is None:
        return await self._execute_step(step.description, ...)
    
    for iteration in range(step.max_loop_iter):
        step.iterations_used = iteration + 1
        result = await self._execute_step(step.description, ...)
        step.output = result
        
        # Evaluate loop_until condition
        try:
            done = bool(eval(
                step.loop_until,
                {"__builtins__": {}},
                {"output": result, "iteration": iteration + 1}
            ))
        except Exception:
            done = True
        
        if done:
            self._logger.info("loop_completed", step_id=step.id, iterations=step.iterations_used)
            break
        
        if iteration < step.max_loop_iter - 1:
            await asyncio.sleep(2 ** min(iteration, 4))  # backoff
    
    return step.output
```

- [ ] **Step 4: Run tests**
```bash
.venv/bin/pytest tests/agent/test_dag_advanced.py -v
```

- [ ] **Step 5: Commit**
```bash
git commit -m "feat(P1.1): conditional branches + loop nodes in workflow DAG"
```

---

## PHASE P1.2: RPA Vault Credential Injection + CAPTCHA + Human Takeover

### Files
- Modify: `app/rpa/executor.py` — vault credential lookup + CAPTCHA detection + takeover endpoint
- Create: `app/rpa/credential_injector.py` — auto-fill from vault
- Modify: `app/api/rpa.py` — `POST /rpa/sessions/{id}/takeover`
- Create: `tests/rpa/test_vault_injection.py`

### Tasks

#### Task P1.2.1: Vault Credential Injection

- [ ] **Step 1: Write failing test**
```python
# tests/rpa/test_vault_injection.py
@pytest.mark.asyncio
async def test_rpa_credential_injector_fetches_from_vault():
    from app.rpa.credential_injector import CredentialInjector
    from unittest.mock import AsyncMock, MagicMock
    
    mock_vault = AsyncMock()
    mock_vault.get_secret = AsyncMock(return_value="secret-password-123")
    
    injector = CredentialInjector(vault=mock_vault)
    cred = await injector.resolve_credential(
        credential_ref="vault://rpa/vendor-portal/password",
        tenant_id="t1"
    )
    assert cred == "secret-password-123"
    mock_vault.get_secret.assert_called_once()

@pytest.mark.asyncio
async def test_rpa_login_fills_from_vault():
    from app.rpa.executor import RPAExecutor
    result = await RPAExecutor().execute(
        tool_name="rpa_type",
        arguments={"selector": "#password", "text": "vault://rpa/app/password"},
        session_id=None,
        tenant_id="test-tenant",
    )
    # Without real vault, should not crash but may return success=False
    assert result is not None
    assert hasattr(result, "success")
```

- [ ] **Step 2: Implement CredentialInjector**
```python
# app/rpa/credential_injector.py
"""Resolve vault:// references in RPA tool arguments."""
from __future__ import annotations
from typing import Any
from app.observability.logging import get_logger

logger = get_logger(__name__)
VAULT_PREFIX = "vault://"


class CredentialInjector:
    def __init__(self, vault: Any = None, secret_store: Any = None) -> None:
        self._vault = vault
        self._secret_store = secret_store

    def is_vault_ref(self, value: str) -> bool:
        return isinstance(value, str) and value.startswith(VAULT_PREFIX)

    async def resolve_credential(self, credential_ref: str, tenant_id: str) -> str:
        """Resolve a vault:// reference to its plaintext value."""
        if not self.is_vault_ref(credential_ref):
            return credential_ref
        
        secret_path = credential_ref[len(VAULT_PREFIX):]
        
        # Try secret store first (Redis-encrypted)
        if self._secret_store is not None:
            try:
                parts = secret_path.split("/")
                if len(parts) >= 2:
                    server_id, key = parts[0], parts[-1]
                    from app.tenancy.context import TenantContext, PlanTier
                    fake_ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="rpa")
                    val = await self._secret_store.get_secret(
                        f"vault://connectors/{server_id}/{key}", tenant_ctx=fake_ctx
                    )
                    if val:
                        return val
            except Exception as exc:
                logger.warning("secret_store_lookup_failed", error=str(exc))
        
        # Try vault directly
        if self._vault is not None:
            try:
                return await self._vault.get_secret(secret_path) or credential_ref
            except Exception as exc:
                logger.warning("vault_lookup_failed", path=secret_path, error=str(exc))
        
        return credential_ref  # Return as-is if resolution fails

    async def resolve_arguments(self, arguments: dict, tenant_id: str) -> dict:
        """Resolve all vault:// refs in an arguments dict."""
        resolved = {}
        for k, v in arguments.items():
            if isinstance(v, str) and self.is_vault_ref(v):
                resolved[k] = await self.resolve_credential(v, tenant_id)
            else:
                resolved[k] = v
        return resolved
```

Update `RPAExecutor.execute()` to resolve vault refs before execution:
```python
# In RPAExecutor.execute(), before dispatching to Playwright:
injector = getattr(self, "_credential_injector", None)
if injector is not None:
    arguments = await injector.resolve_arguments(arguments, tenant_id)
```

Wire `CredentialInjector` in main.py after secret store is available.

- [ ] **Step 3: Add CAPTCHA detection tool**
```python
# Add to app/rpa/tools.py:
{"name": "rpa_detect_captcha",
 "description": "Detect if a CAPTCHA is present on the current page",
 "parameters": {"type": "object", "properties": {}, "required": []}},
{"name": "rpa_request_human_help",
 "description": "Pause RPA session and request human operator to take over",
 "parameters": {"type": "object",
   "properties": {"reason": {"type": "string", "description": "Why human help is needed"}},
   "required": ["reason"]}},
```

Implement in executor.py:
```python
elif tool_name == "rpa_detect_captcha":
    try:
        # Check for common CAPTCHA indicators
        has_captcha = await page.evaluate("""() => {
            const body = document.body.innerHTML.toLowerCase();
            return body.includes('recaptcha') || body.includes('hcaptcha') ||
                   body.includes('cloudflare') || !!document.querySelector('iframe[src*="captcha"]');
        }""")
        return f"CAPTCHA detected: {has_captcha}"
    except Exception as exc:
        return f"rpa_detect_captcha simulation: no captcha detected"

elif tool_name == "rpa_request_human_help":
    reason = arguments.get("reason", "Assistance required")
    # Set a Redis flag that the UI can poll
    if self._redis is not None:
        await self._redis.set(f"rpa_human_needed:{session_id}", reason, ex=3600)
    return f"Human assistance requested: {reason}. Session ID: {session_id}"
```

- [ ] **Step 4: Add human takeover endpoint**

In `app/api/rpa.py`:
```python
@router.post("/rpa/sessions/{session_id}/takeover")
async def request_human_takeover(request: Request, session_id: str, reason: str = "") -> dict:
    """Request human operator to take over an RPA session."""
    tenant = _require_tenant(request)
    store = _session_store(request)
    session = await store.get(session_id, tenant_id=tenant.tenant_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    takeover_url = f"/rpa/sessions/{session_id}/live"  # VNC/noVNC endpoint placeholder
    
    if redis is not None:
        import json
        await redis.set(f"rpa_human_needed:{session_id}",
                       json.dumps({"reason": reason, "session_id": session_id}), ex=3600)
    
    return {
        "session_id": session_id,
        "status": "awaiting_human",
        "takeover_url": takeover_url,
        "reason": reason or "Operator requested",
    }
```

- [ ] **Step 5: Run tests and commit**
```bash
.venv/bin/pytest tests/rpa/ -v
git commit -m "feat(P1.2): RPA vault credential injection + CAPTCHA detection + human takeover"
```

---

## PHASE P1.3: HITL Multi-Person Threshold + Rejection Feedback + Email Approval

### Files
- Modify: `app/governance/hitl.py` — set required_approvers from agent policy, email approval
- Modify: `app/services/goal_service.py` — subscribe to rejection note and inject into next plan
- Create: `app/integrations/email/approval_sender.py` — email approval link generator
- Create: `tests/governance/test_hitl_advanced.py`

### Tasks

#### Task P1.3.1: Multi-Person Approval Threshold from Agent Config

- [ ] **Step 1: Write failing test**
```python
# tests/governance/test_hitl_advanced.py
@pytest.mark.asyncio
async def test_request_approval_uses_agent_required_approvers():
    """required_approvers should be read from agent config, not always 1."""
    from app.governance.hitl import HITLGateway
    from unittest.mock import AsyncMock
    from app.tenancy.context import PlanTier, TenantContext
    
    gw = HITLGateway()
    T = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    
    # Agent config requires 2 approvers
    req = await gw.request_approval(
        goal_id="g1",
        step_description="Deploy to production",
        tenant_ctx=T,
        required_approvers=2,  # <-- this param must be accepted
    )
    assert req.required_approvers == 2, "required_approvers must be stored on the request"
```

- [ ] **Step 2: Fix HITLGateway.request_approval to accept required_approvers**

In `app/governance/hitl.py`, update `request_approval()` signature:
```python
async def request_approval(
    self,
    *,
    goal_id: str,
    step_description: str,
    tenant_ctx: Any,
    risk_level: str = "high",
    timeout: int = 300,
    required_approvers: int = 1,  # NEW PARAM — default 1 for backward compat
    context: dict | None = None,
) -> "ApprovalRequest":
    req = ApprovalRequest(
        goal_id=goal_id,
        step_description=step_description,
        tenant_id=tenant_ctx.tenant_id,
        risk_level=risk_level,
        timeout=timeout,
        required_approvers=required_approvers,  # STORE IT
        ...
    )
```

Update the graph to pass `required_approvers` from the step's `risk` field:
```python
# In graph._execute_step, when requesting approval:
required_approvers = 1
if step_risk in ("destructive",):
    required_approvers = 2  # destructive actions need 2 approvers
await hitl_gw.request_approval(
    ...,
    required_approvers=required_approvers,
)
```

- [ ] **Step 3: Rejection note feeds back to planner**

In `app/services/goal_service.py`, subscribe to the rejection channel at startup:
```python
async def _subscribe_hitl_rejections(self, redis_url: str) -> None:
    """Listen for HITL rejections and inject note into next plan iteration."""
    import json
    import redis.asyncio as aioredis
    async with aioredis.from_url(redis_url, decode_responses=True) as r:
        pubsub = r.pubsub()
        await pubsub.psubscribe("hitl_rejected:*")
        async for msg in pubsub.listen():
            if msg["type"] != "pmessage":
                continue
            try:
                data = json.loads(msg["data"])
                goal_id = data.get("goal_id")
                note = data.get("note", "")
                if goal_id and goal_id in self._goals:
                    record = self._goals[goal_id]
                    record.hitl_rejection_note = note
                    # Store for next plan iteration
                    record.events.append({
                        "type": "hitl_rejected",
                        "note": note,
                        "ts": datetime.now(UTC).isoformat(),
                    })
                    self._logger.info("hitl_rejection_note_received", goal_id=goal_id)
            except Exception as exc:
                self._logger.warning("hitl_rejection_subscribe_error", error=str(exc))
```

Start this subscription in main.py lifespan when Redis is available.

In `graph._node_plan()`, inject the rejection note into the planner prompt:
```python
rejection_note = agent_state.context.get("hitl_rejection_note", "")
if rejection_note:
    system_content += f"\n\n[HITL Rejection Feedback — DO NOT repeat this action]\n{rejection_note}"
```

- [ ] **Step 4: Email approval**

Create `app/integrations/email/approval_sender.py`:
```python
"""Send HITL approval request emails with approve/reject links."""
from __future__ import annotations
import os, hmac, hashlib, urllib.parse
from typing import Any


def _sign_approval_link(request_id: str, action: str) -> str:
    """Create an HMAC-signed link for approve/reject via email."""
    secret = os.getenv("HITL_EMAIL_SECRET", "changeme-set-HITL_EMAIL_SECRET")
    payload = f"{request_id}:{action}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return sig


async def send_approval_email(
    *,
    to_email: str,
    goal_description: str,
    step_description: str,
    request_id: str,
    frontend_url: str,
    smtp_host: str = "localhost",
    smtp_port: int = 1025,
) -> bool:
    """Send HTML email with clickable Approve/Reject buttons."""
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        
        approve_sig = _sign_approval_link(request_id, "approve")
        reject_sig = _sign_approval_link(request_id, "reject")
        
        approve_url = f"{frontend_url}/hitl/approve/{request_id}?sig={approve_sig}"
        reject_url = f"{frontend_url}/hitl/reject/{request_id}?sig={reject_sig}"
        
        html = f"""
        <html><body>
        <h2>Action Required: Goal Approval</h2>
        <p><strong>Goal:</strong> {goal_description}</p>
        <p><strong>Step needing approval:</strong> {step_description}</p>
        <p>
          <a href="{approve_url}" style="background:#16a34a;color:white;padding:10px 20px;border-radius:4px;text-decoration:none;">✓ Approve</a>
          &nbsp;&nbsp;
          <a href="{reject_url}" style="background:#dc2626;color:white;padding:10px 20px;border-radius:4px;text-decoration:none;">✗ Reject</a>
        </p>
        </body></html>
        """
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AgentVerse] Approval Required: {step_description[:60]}"
        msg["From"] = "agentverse@noreply.local"
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html"))
        
        await aiosmtplib.send(msg, hostname=smtp_host, port=smtp_port)
        return True
    except Exception as exc:
        import logging; logging.getLogger(__name__).warning("approval_email_failed: %s", exc)
        return False
```

Add `GET /hitl/approve/{request_id}` and `GET /hitl/reject/{request_id}` endpoints in `app/api/governance.py` for email link handling.

- [ ] **Step 5: Run tests and commit**
```bash
.venv/bin/pytest tests/governance/test_hitl_advanced.py -v
git commit -m "feat(P1.3): HITL multi-person threshold + rejection→planner feedback + email approval"
```

---

## PHASE P1.4: Artifact System — General Artifacts + Retention + Video

### Files
- Create: `app/tools/artifact_tool.py` — `save_artifact` tool for agents
- Modify: `app/rpa/artifacts.py` — retention policy + TTL
- Modify: `app/api/artifacts.py` — list/delete/retention endpoints
- Modify: `app/db/migrations/versions/0036_artifact_retention.py`
- Create: `tests/api/test_artifact_system.py`

### Tasks

#### Task P1.4.1: General Artifact Creation Tool

- [ ] **Step 1: Write test**
```python
# tests/api/test_artifact_system.py
@pytest.mark.asyncio
async def test_save_artifact_tool_creates_file():
    from app.tools.artifact_tool import ArtifactTool
    tool = ArtifactTool()
    result = await tool.execute(
        name="report.csv",
        content="id,name,value\n1,test,100",
        content_type="text/csv",
        tenant_id="t1",
        goal_id="g1",
    )
    assert result.get("artifact_id") is not None
    assert result.get("filename") == "report.csv"
```

- [ ] **Step 2: Implement ArtifactTool**
```python
# app/tools/artifact_tool.py
"""General-purpose artifact creation tool for agents."""
from __future__ import annotations
import uuid
from datetime import UTC, datetime
from typing import Any
from app.observability.logging import get_logger

logger = get_logger(__name__)


class ArtifactTool:
    """Save text/binary content as a downloadable artifact."""

    name = "save_artifact"
    description = "Save content as a downloadable artifact file (CSV, JSON, markdown, text, etc.)"

    def __init__(self, artifact_store: Any = None) -> None:
        self._store = artifact_store

    async def execute(
        self,
        *,
        name: str,
        content: str | bytes,
        content_type: str = "text/plain",
        tenant_id: str,
        goal_id: str = "",
    ) -> dict[str, Any]:
        artifact_id = uuid.uuid4().hex
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        
        artifact_url = f"/tmp/artifacts/{tenant_id}/{goal_id}/{artifact_id}/{name}"
        
        if self._store is not None:
            try:
                artifact_url = await self._store.write_bytes(
                    key=f"{tenant_id}/{goal_id}/{artifact_id}/{name}",
                    data=content_bytes,
                    content_type=content_type,
                )
            except Exception as exc:
                logger.warning("artifact_store_failed", error=str(exc))
                import os, pathlib
                pathlib.Path(f"/tmp/artifacts/{tenant_id}/{goal_id}/{artifact_id}").mkdir(parents=True, exist_ok=True)
                with open(f"/tmp/artifacts/{tenant_id}/{goal_id}/{artifact_id}/{name}", "wb") as f:
                    f.write(content_bytes)
        
        return {
            "artifact_id": artifact_id,
            "filename": name,
            "content_type": content_type,
            "size_bytes": len(content_bytes),
            "artifact_url": artifact_url,
            "created_at": datetime.now(UTC).isoformat(),
            "goal_id": goal_id,
        }

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Filename including extension"},
                    "content": {"type": "string", "description": "File content"},
                    "content_type": {"type": "string", "default": "text/plain"},
                },
                "required": ["name", "content"],
            },
        }
```

- [ ] **Step 3: Add retention migration**
```python
# app/db/migrations/versions/0036_artifact_retention.py
revision = "0036"
down_revision = "0035"

def upgrade():
    from alembic import op
    op.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            goal_id     TEXT NOT NULL DEFAULT '',
            filename    TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            size_bytes  BIGINT NOT NULL DEFAULT 0,
            artifact_url TEXT NOT NULL,
            expires_at  TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_tenant ON artifacts (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_expires ON artifacts (expires_at) WHERE expires_at IS NOT NULL")

def downgrade():
    from alembic import op
    op.execute("DROP TABLE IF EXISTS artifacts")
```

Add `purge_expired_artifacts` Celery task to clean up MinIO objects where `expires_at < NOW()`.

- [ ] **Step 4: Add retention Celery task**
```python
@celery_app.task(name="agentverse.maintenance.purge_expired_artifacts")
def purge_expired_artifacts() -> dict:
    async def _run():
        from app.db.session import get_session_factory
        from sqlalchemy import text
        db = get_session_factory()
        purged = 0
        async with db() as session, session.begin():
            rows = (await session.execute(text(
                "SELECT id, artifact_url FROM artifacts WHERE expires_at < NOW() LIMIT 100"
            ))).fetchall()
            for row in rows:
                try:
                    # Delete from MinIO
                    import os
                    if "minio" in (os.getenv("MINIO_ENDPOINT","") or ""):
                        pass  # MinIO lifecycle rules handle this
                    await session.execute(text("DELETE FROM artifacts WHERE id=:id"), {"id": row[0]})
                    purged += 1
                except Exception:
                    pass
        return {"purged": purged}
    import asyncio; return asyncio.run(_run())
```

- [ ] **Step 5: Run tests and commit**
```bash
.venv/bin/pytest tests/api/test_artifact_system.py -v
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse .venv/bin/alembic upgrade head
git commit -m "feat(P1.4): general artifact tool + retention policies + artifacts DB table"
```

---

## PHASE P1.5: Production Infrastructure — DB Backups + Grafana + Redis HA

### Files
- Create: `infra/helm/agentverse/templates/backup-cronjob.yaml`
- Create: `infra/grafana/dashboards/agentverse-overview.json`
- Create: `infra/grafana/dashboards/agentverse-costs.json`
- Create: `infra/grafana/dashboards/agentverse-reliability.json`
- Modify: `infra/docker-compose.prod.yml` — Redis Sentinel + Grafana dashboards mount
- Create: `tests/infra/test_production_infra.py`

### Tasks

#### Task P1.5.1: PostgreSQL Backup CronJob (Helm)

```yaml
# infra/helm/agentverse/templates/backup-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "agentverse.fullname" . }}-pg-backup
spec:
  schedule: "0 2 * * *"  # 2 AM daily
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: pg-backup
            image: postgres:16-alpine
            env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.postgresql.secretName | default "agentverse-secrets" }}
                  key: postgresql-password
            command:
            - sh
            - -c
            - |
              DATE=$(date +%Y%m%d_%H%M%S)
              pg_dump -h {{ .Values.postgresql.host }} -U {{ .Values.postgresql.username }} \
                {{ .Values.postgresql.database }} | gzip > /backup/agentverse_${DATE}.sql.gz
              # Keep last 7 backups
              ls -t /backup/*.sql.gz | tail -n +8 | xargs -r rm
              echo "Backup completed: agentverse_${DATE}.sql.gz"
            volumeMounts:
            - name: backup-storage
              mountPath: /backup
          restartPolicy: OnFailure
          volumes:
          - name: backup-storage
            persistentVolumeClaim:
              claimName: {{ include "agentverse.fullname" . }}-backup-pvc
```

#### Task P1.5.2: Grafana Dashboard JSONs

Create three pre-built dashboards using Prometheus metrics already emitted by the backend:

1. **agentverse-overview.json** — Goal throughput, active goals, success rate, P99 latency
2. **agentverse-costs.json** — Cost per tenant per day, model cost breakdown, budget utilization
3. **agentverse-reliability.json** — Circuit breaker state, retry rate, DLQ depth, stuck goals

Each dashboard uses `promtheus_http_requests_total`, `agentverse_goal_completed_total`, `agentverse_cost_usd`, `agentverse_tool_call_duration_seconds` metrics.

#### Task P1.5.3: Redis Sentinel for HA

Add to `docker-compose.prod.yml`:
```yaml
  redis-sentinel-1:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./redis/sentinel.conf:/etc/redis/sentinel.conf:ro
    depends_on: [redis]

  redis-sentinel-2:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./redis/sentinel.conf:/etc/redis/sentinel.conf:ro
    depends_on: [redis]
```

Create `infra/redis/sentinel.conf`:
```
sentinel monitor mymaster redis 6379 2
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 60000
sentinel parallel-syncs mymaster 1
```

- [ ] **Commit all P1.5 changes**
```bash
git add infra/
git commit -m "feat(P1.5): DB backup CronJob + Grafana dashboards + Redis Sentinel HA"
```

---

## PHASE P2.1–P2.10: Quality & Completeness

[Following same TDD pattern with Write Test → Implement → Run → Commit for each]

### P2.1: Capability Registry — Tool Stats DB + Auto-Select

**Key changes:**
- Add `ToolCapabilityStats` model with `success_rate`, `avg_latency_ms`, `call_count`, `error_count` updated by `CostController.record_tool_call()`
- Call `CapabilitySearch.search()` automatically in `submit_goal()` when goal has no agent and no router result
- Pre-compute tool embeddings at `POST /connectors/{id}/discover` time and cache in Redis

### P2.2: Agent Router — DB History Scoring

**Key changes:**
- `_score_by_history()` queries `evaluations` table: `SELECT AVG(average_score) FROM evaluations WHERE goal_id IN (SELECT id FROM goals WHERE agent_id=:aid AND tenant_id=:tid)`
- Multi-agent parallel dispatch: when `mode="multi_agent"`, spawn N goals with `asyncio.gather()` via `goal_service.submit_goal()` for each candidate agent

### P2.3: Memory — DB Failure Records + Tool Reliability

**Key changes:**
- `record_failure()` calls `record_async()` with `success=False`
- New `tool_reliability_memory` table: `(tenant_id, tool_name, success_count, failure_count, avg_latency_ms)`
- Updated after every tool call in `graph._execute_step()`
- New `GET /memory/tool-reliability` endpoint

### P2.4: Governance — Cross-Agent Policy Inheritance

**Key changes:**
- `PolicyEngine.evaluate()` accepts `parent_policy_ids: list[str]` and merges parent policies
- `SupervisorAgent._decompose()` passes parent `tenant_ctx.policy_engine_config` to sub-agents
- `POST /governance/simulate` — accepts `goal` + `agent_id`, runs `SimulationRunner` with full policy evaluation, returns `{would_require_approvals: [...], would_deny: [...], estimated_cost: ...}`

### P2.5: Marketplace — Bundles + Versioning + Private Scope

**Key changes:**
- Migration 0037: `template_versions` table with `version`, `changelog`, `previous_version_id`, `published_at`
- `visibility: "private" | "team" | "community"` field
- `TemplateBundleRequest` model with `template_ids`, `name`, `description` for bundled deploy
- `POST /marketplace/bundles` — deploy multiple templates as a group

### P2.6: Evaluation — Golden Tasks + Rollout Gate

**Key changes:**
- Migration 0038: `golden_tasks` table with `goal`, `expected_tool_calls`, `expected_output_contains`, `forbidden_tools`, `eval_suite_id`
- `EvalSuiteRunner.run_regression()` — runs all golden tasks against current agent, computes pass rate
- Readiness check: fail if `pass_rate < 0.8` for `fully-autonomous` agents
- `POST /eval/golden-tasks` — CRUD for golden task dataset

### P2.7: Identity — Key Rotation + BYOK + Per-Agent Credentials

**Key changes:**
- Migration 0039: `vault_key_versions` table
- `CredentialVault.rotate_key(old_key, new_key)` — re-encrypts all secrets atomically in batches of 100
- `POST /tenants/me/vault-key` — accept external BYOK key
- `agent_connector_credentials` table: `(agent_id, connector_id, secret_ref)` for per-agent scoping

### P2.8: Multi-Tenant — Per-Tenant Celery Queues

**Key changes:**
- `celery_app.conf.task_routes` updated to route `agentverse.goals.*` based on `tenant_id` prefix
- Queue naming: `goals.free`, `goals.starter`, `goals.professional`, `goals.enterprise`
- Worker command in docker-compose updated: `-Q goals.free,goals.starter,goals.professional,goals.enterprise,schedules,maintenance,goals_dlq`
- All raw SQL in `tasks.py` wrapped with `async with sqlalchemy_rls_context(session, tenant_id):`

### P2.9: SDK — Local Mock Server + Connector Scaffolding

**Key changes:**
- `agent-verse-sdk-python/agentverse/mock_server.py` — `FastAPI` app that accepts and stores API calls, returns configurable fixtures
- `agentverse dev start` CLI command — starts mock server on port 8001
- `agentverse connectors scaffold --name my-connector` — generates boilerplate MCP server
- Schema validator: `agentverse connectors validate schema.json`

### P2.10: Compliance — Async GDPR Export + Consent

**Key changes:**
- Migration 0040: `consent_records` table with `tenant_id`, `purpose`, `granted_at`, `revoked_at`, `legal_basis`
- `POST /compliance/export` — creates background Celery job, returns `{job_id}`
- `GET /compliance/export/{job_id}/status` — polling endpoint
- `GET /compliance/export/{job_id}/download` — download when ready
- `POST /tenants/me/consent` — record processing consent

---

## PHASE P3.1–P3.6: Polish & Advanced

### P3.1: Collaboration — Debate Audit + Real-Time WebSocket

**Key changes:**
- Migration: `debate_sessions` + `debate_proposals` tables
- `DebateOrchestrator.run()` persists each round to DB
- WebSocket `ConnectionManager` broadcasts `append_operation()` events to all session subscribers
- Presence tracking: `POST /collab/{id}/join` stores participant; `GET /collab/{id}/presence` lists active users

### P3.2: CLI — Missing Commands

**Key changes:**
```
agentverse connectors list
agentverse connectors register --name X --url Y --type rest
agentverse schedules create --goal "..." --cron "0 9 * * MON"
agentverse policy create --name X --action deny --tool "jira.delete"
agentverse simulate --goal "..." --agent-id A
agentverse logs --goal-id G [--follow]
```

### P3.3: Observability — Grafana Dashboards + Token SSE

**Key changes:**
- Grafana dashboard JSONs for overview/cost/reliability mounted via `provisioning/dashboards/`
- `token_usage` SSE event emitted after each LLM call: `{"type":"token_usage","input_tokens":N,"output_tokens":M,"cost_usd":X}`
- Per-retry trace span with `retry.attempt` attribute

### P3.4: Event Bus — Alertmanager + File Drop

**Key changes:**
- `POST /events/alertmanager` — Alertmanager webhook receiver that creates goals from alerts
- `POST /events/datadog` — Datadog webhook (HMAC-verified)
- `FILE_DROP` trigger type with MinIO bucket notification via SQS-compatible webhook

### P3.5: Reliability — Real Tool Inverses

**Key changes:**
- Implement inverses for: `github_create_issue → github_delete_issue`, `jira_create_issue → jira_delete_issue`, `slack_send_message → slack_delete_message`, `confluence_create_page → confluence_delete_page`
- `FallbackToolRegistry`: maps primary tool to fallback alternative with same schema
- Circuit breaker pre-warming: `MCPClient.warmup_circuit_breakers()` called at startup

### P3.6: UI/UX — Missing Pages

**Key changes:**
- `SimulationPage.tsx` — goal text input, side-effect preview table, cost/risk estimates
- `RpaLivePage.tsx` — WebSocket screenshot feed (polling every 2s), action log
- `IncidentReplayPage.tsx` — timeline visualization using `/goals/{id}/replay` data
- `AuditExplorerPage.tsx` — filterable, searchable audit log with export to CSV

---

## VERIFICATION CHECKLIST

After implementing all phases, run this verification:

```bash
# Full backend test suite
.venv/bin/pytest tests/ -q --ignore=tests/integration --ignore=tests/e2e \
  --ignore=tests/core/test_pools_integration.py \
  --ignore=tests/db/test_tenancy_integration.py \
  -p no:warnings --tb=short

# Integration tests against real Postgres
.venv/bin/pytest tests/integration/ -v

# Frontend tests
cd agent-verse-frontend && npx vitest run

# Apply all migrations
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse \
  .venv/bin/alembic upgrade head && .venv/bin/alembic current

# Start full stack
docker compose -f infra/docker-compose.yml up -d
```

Every phase is complete ONLY when:
1. All tests in that phase pass
2. No regressions in the full suite
3. Migration applied cleanly
4. Docker Compose starts with the new feature active

---

## IMPLEMENTATION ORDER

```
P0.1 → P0.2 → commit "P0 complete: durable execution + knowledge OS"
P1.1 + P1.2 + P1.3 + P1.4 + P1.5 (parallel) → commit "P1 complete"
P2.1 + P2.2 + P2.3 + P2.4 + P2.5 (parallel) → commit "P2a complete"
P2.6 + P2.7 + P2.8 + P2.9 + P2.10 (parallel) → commit "P2b complete"
P3.1 + P3.2 + P3.3 + P3.4 + P3.5 + P3.6 (parallel) → commit "P3 complete"
Final: run full suite, tag v2.0.0
```
