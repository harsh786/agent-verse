"""Bind the guardrail engine to the tenant's persisted rules in a Celery worker.

The FastAPI lifespan binds :data:`app.guardrails_v2.engine.guardrails_engine` to
the Postgres rule repository; that lifespan never runs in a worker. Without a
repository ``ensure_tenant_loaded`` is a no-op, so every worker-run goal,
workflow and ingestion was screened against the in-memory baseline defaults
only — a tenant's own rules (a PII ``redact`` rule, a keyword ``block`` rule)
never applied on the worker (RV-06 for repository ingest, P8-1 for goals and
workflows).

Each worker path calls :func:`bind_worker_guardrail_rules` before it runs
anything a guardrail screens. Each tenant's rules then load lazily under that
tenant's RLS context on its first evaluation and refresh every
``rule_refresh_s`` (rules written through any API replica reach the worker).
"""

from __future__ import annotations

from typing import Any


def _current_task_session() -> Any:
    """A session from the CURRENT process-wide factory.

    Celery tasks run each ``_run_async`` on a fresh event loop and dispose the
    task engine at its end, so the repository must not pin one factory (its
    pooled connections belong to a closed loop); it resolves the live factory
    on every session instead.
    """
    from app.db.session import get_session_factory

    return get_session_factory()()


def bind_worker_guardrail_rules(db_factory: Any = None, *, engine: Any = None) -> bool:
    """Bind the engine (default: the process singleton) to the Postgres rule store.

    Idempotent: an engine that already has a repository is left alone. Returns
    True when this call bound it. The worker writes no rules, so nothing is
    auto-persisted. A failure propagates — nothing a guardrail screens may run
    against the defaults only while claiming the tenant's rules applied.
    """
    from app.guardrails_v2.repository import PostgresGuardrailRuleRepository

    if engine is None:
        from app.guardrails_v2.engine import guardrails_engine

        engine = guardrails_engine
    if engine.has_repository:
        return False
    engine.bind_repository(
        PostgresGuardrailRuleRepository(db_factory or _current_task_session),  # type: ignore[arg-type]
        auto_persist=False,
    )
    return True


__all__ = ["bind_worker_guardrail_rules"]
