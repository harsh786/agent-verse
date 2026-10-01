"""Cost services for LLM decision calls made in Celery workers (PROV-05).

``run_goal`` used to install ``set_platform_cost_services(lambda: (cost, None))``
as a module global on every run: no CostTracker (so worker decision calls wrote
no ledger rows) and last-writer-wins across tasks. Instead the worker installs
ONE resolver at process start (:func:`install_worker_cost_services`) that hands
each event loop — i.e. each task's ``asyncio.run`` — its own Redis-backed
controller and ledger tracker (async Redis / DB clients are loop-bound).

Charges are attributed per call: inside a goal the goal scope applies; any other
task that serves one tenant (a ``tenant_id`` argument) runs under
:func:`app.providers.guarded_completion.tenant_charge_scope` via the
``task_prerun`` hook below. A decision call with neither is refused
(``complete_decision`` fails closed when cost services are configured).
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import os
import weakref
from typing import Any

logger = logging.getLogger(__name__)

_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[Any, Any]] = (
    weakref.WeakKeyDictionary()
)
# In-memory fallback (no Redis): one per process, so a tenant's daily spend is
# at least accumulated across this worker's tasks instead of reset per task.
_process_controller: Any = None
_task_tokens: dict[str, contextvars.Token[Any]] = {}


def _build() -> tuple[Any, Any]:
    """A controller + ledger tracker bound to the running event loop."""
    global _process_controller
    from app.governance.cost import CostController, RedisCostController
    from app.intelligence.cost_tracker import CostTracker

    redis_client: Any = None
    redis_url = os.getenv("REDIS_URL", "")
    if redis_url:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(redis_url, decode_responses=True)
    db_factory: Any = None
    try:
        from app.db.session import get_session_factory

        db_factory = get_session_factory()
    except Exception as exc:  # no DB configured: budgets use defaults, ledger is Redis-only
        logger.warning("worker_cost_db_unavailable: %s", str(exc)[:160])
    controller: Any
    if redis_client is not None:
        controller = RedisCostController(redis=redis_client)
    else:
        if _process_controller is None:
            _process_controller = CostController()
        controller = _process_controller
    if db_factory is not None:
        controller.set_budget_db(db_factory)
    return controller, CostTracker(redis=redis_client, db_factory=db_factory)


def worker_cost_services() -> tuple[Any, Any]:
    """``() -> (cost_controller, cost_tracker)`` for the running loop (cached per loop)."""
    loop = asyncio.get_running_loop()
    services = _by_loop.get(loop)
    if services is None:
        services = _build()
        _by_loop[loop] = services
    return services


def install_worker_cost_services() -> None:
    from app.providers.guarded_completion import set_platform_cost_services

    set_platform_cost_services(worker_cost_services)


def _task_tenant_id(task: Any, args: Any, kwargs: Any) -> str | None:
    tid = (kwargs or {}).get("tenant_id")
    if tid:
        return str(tid)
    try:
        bound = inspect.signature(task.run).bind_partial(*(args or ()), **(kwargs or {}))
    except (TypeError, ValueError):
        return None
    tid = bound.arguments.get("tenant_id")
    return str(tid) if tid else None


def enter_task_tenant_scope(task_id: str, task: Any, args: Any, kwargs: Any) -> None:
    """``task_prerun``: charge the task's tenant for tenant-less decision calls."""
    tid = _task_tenant_id(task, args, kwargs)
    if not tid:
        return
    from types import SimpleNamespace

    from app.providers.guarded_completion import _tenant_scope

    _task_tokens[task_id] = _tenant_scope.set(SimpleNamespace(tenant_id=tid))


def exit_task_tenant_scope(task_id: str) -> None:
    """``task_postrun``: restore the previous scope."""
    token = _task_tokens.pop(task_id, None)
    if token is None:
        return
    from app.providers.guarded_completion import _tenant_scope

    try:
        _tenant_scope.reset(token)
    except ValueError:  # reset from another context (should not happen in prefork)
        _tenant_scope.set(None)
