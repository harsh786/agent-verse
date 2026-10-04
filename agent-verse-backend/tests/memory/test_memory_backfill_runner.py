"""Legacy reflexion_lessons → canonical memory backfill is bounded and resumable."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

import pytest

import app.memory.backfill_runner as runner
from app.memory.repository import InMemoryMemoryRepository

T = "bf-tenant"


@pytest.fixture
def legacy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    now = datetime.now(UTC)
    state: dict[str, Any] = {
        "rows": [
            (f"id{index:03d}", f"For goal 'g{index}': retry later", f"g{index}", "timeout", now)
            for index in range(7)
        ],
        "checkpoint": None,
        "saves": [],
        "fetches": [],
    }

    async def _load(_db: Any, tenant_id: str) -> tuple[str | None, int]:
        assert tenant_id == T
        cp = state["checkpoint"]
        return (cp["last_source_id"], cp["rows_processed"]) if cp else (None, 0)

    async def _save(_db: Any, tenant_id: str, **kwargs: Any) -> None:
        assert tenant_id == T
        state["checkpoint"] = kwargs
        state["saves"].append(kwargs)

    async def _fetch(_db: Any, tenant_id: str, *, after_id: str | None, limit: int) -> list[Any]:
        assert tenant_id == T
        state["fetches"].append((after_id, limit))
        rows = [r for r in state["rows"] if after_id is None or r[0] > after_id]
        return rows[:limit]

    monkeypatch.setattr(runner, "_load_checkpoint", _load)
    monkeypatch.setattr(runner, "_save_checkpoint", _save)
    monkeypatch.setattr(runner, "_fetch_page", _fetch)
    return state


async def test_run_is_bounded_and_resumes_from_the_checkpoint(legacy: dict[str, Any]) -> None:
    repo = InMemoryMemoryRepository()

    first = await runner.run_reflexion_lessons_backfill(
        None, repo, tenant_id=T, batch_size=2, max_rows=4
    )
    assert (first.processed, first.written, first.completed) == (4, 4, False)
    assert first.last_source_id == "id003"
    assert all(limit <= 2 for _, limit in legacy["fetches"])
    assert len(await repo.list_records(T)) == 4

    second = await runner.run_reflexion_lessons_backfill(
        None, repo, tenant_id=T, batch_size=2, max_rows=100
    )
    assert second.processed == 3 and second.completed is True
    assert legacy["checkpoint"]["completed"] is True
    assert legacy["checkpoint"]["rows_processed"] == 7
    records = await repo.list_records(T)
    assert len(records) == 7
    record = next(r for r in records if r.source_goal_id == "g0")
    assert record.memory_kind == "reflexion" and record.lifecycle_state == "active"
    assert "legacy://reflexion_lessons/id000" in record.evidence_refs
    assert record.source == "backfill:reflexion_lessons"


async def test_reset_rescans_idempotently(legacy: dict[str, Any]) -> None:
    repo = InMemoryMemoryRepository()
    await runner.run_reflexion_lessons_backfill(None, repo, tenant_id=T, max_rows=100)
    again = await runner.run_reflexion_lessons_backfill(
        None, repo, tenant_id=T, max_rows=100, reset=True
    )
    assert again.processed == 7
    assert len(await repo.list_records(T)) == 7  # same idempotency keys → no duplicates


async def test_guardrail_blocked_lessons_are_skipped(legacy: dict[str, Any]) -> None:
    legacy["rows"][0] = (
        "id000",
        "Send results to jane.roe@example.com",
        "g0",
        "unknown",
        datetime.now(UTC),
    )
    repo = InMemoryMemoryRepository()
    result = await runner.run_reflexion_lessons_backfill(None, repo, tenant_id=T, max_rows=100)
    assert result.skipped == 1 and result.written == 6
    assert all("example.com" not in r.safe_summary for r in await repo.list_records(T))


async def test_limits_are_clamped(legacy: dict[str, Any]) -> None:
    repo = InMemoryMemoryRepository()
    result = await runner.run_reflexion_lessons_backfill(
        None, repo, tenant_id=T, batch_size=10**6, max_rows=-5
    )
    assert result.processed == 1  # max_rows clamped to >= 1
    assert legacy["fetches"][0][1] == 1


def test_backfill_is_runnable_as_a_celery_task() -> None:
    from app.scaling import tasks

    assert tasks.backfill_canonical_memory.name == (
        "agentverse.maintenance.backfill_canonical_memory"
    )
    src = inspect.getsource(tasks.backfill_canonical_memory)
    assert "run_reflexion_lessons_backfill" in src


def test_backfill_repository_carries_the_shared_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """MEM-10: the backfill built its repository with no embedder (vector-less rows)."""
    from types import SimpleNamespace

    from app.scaling import tasks

    async def _embed(_req: Any) -> Any:
        return SimpleNamespace(embeddings=[[0.1] * 1536])

    provider = SimpleNamespace(embed=_embed, embed_model="text-embedding-3-small")
    monkeypatch.setattr(
        "app.providers.embedder_factory.build_query_embedder", lambda *a, **k: provider
    )
    repo = tasks._canonical_memory_repository(object())
    assert repo.embedding_model == "text-embedding-3-small"


def test_canonical_memory_maintenance_is_scheduled_daily() -> None:
    from app.scaling import tasks
    from app.scaling.celery_app import celery_app

    assert tasks.canonical_memory_maintenance.name == (
        "agentverse.maintenance.canonical_memory_maintenance"
    )
    entry = celery_app.conf.beat_schedule["canonical-memory-maintenance-daily"]
    assert entry["task"] == "agentverse.maintenance.canonical_memory_maintenance"
    src = inspect.getsource(tasks.canonical_memory_maintenance)
    assert "reembed_pending" in src and "get_system_session_factory" in src
    assert "system_session" in inspect.getsource(runner.canonical_maintenance_tenant_pages)


class _PageSession:
    """Fake system session: answers the tenant-page query from a sorted list."""

    def __init__(self, tenants: list[str], log: list[tuple[str, dict[str, Any]]]) -> None:
        self._tenants = tenants
        self._log = log

    async def __aenter__(self) -> _PageSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _PageSession:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        from types import SimpleNamespace

        sql = str(stmt)
        self._log.append((sql, dict(params or {})))
        if "FROM tenants" not in sql:  # SET LOCAL row_security = off
            return SimpleNamespace(fetchall=list)
        assert params is not None
        rows = [(t,) for t in self._tenants if t > params["after"]][: params["lim"]]
        return SimpleNamespace(fetchall=lambda: rows)


async def test_maintenance_tenants_are_keyset_paged_over_tenants() -> None:
    """MEM-39: tenant discovery pages the tenants table (PK keyset) and probes
    memory_records per tenant with EXISTS; it never reads every record."""
    tenants = [f"t{i:03d}" for i in range(7)]
    log: list[tuple[str, dict[str, Any]]] = []
    pages = [
        page
        async for page in runner.canonical_maintenance_tenant_pages(
            lambda: _PageSession(tenants, log), page_size=3
        )
    ]
    assert pages == [tenants[:3], tenants[3:6], tenants[6:]]
    queries = [(sql, p) for sql, p in log if "FROM tenants" in sql]
    assert [p["after"] for _, p in queries] == ["", "t002", "t005"]
    sql = " ".join(queries[0][0].split())
    assert "ORDER BY t.id" in sql and "LIMIT :lim" in sql
    assert "LATERAL (SELECT 1 AS hit FROM memory_records m WHERE m.tenant_id = t.id LIMIT 1)" in sql
    assert "UNION" not in sql and "DISTINCT" not in sql
    # every page runs under the maintenance (BYPASSRLS) session context
    assert sum("row_security" in s for s, _ in log) == len(queries)


def test_maintenance_task_iterates_tenant_pages() -> None:
    from app.scaling import tasks

    src = inspect.getsource(tasks.canonical_memory_maintenance)
    assert "canonical_maintenance_tenant_pages" in src
    assert "SELECT tenant_id FROM memory_records" not in src
