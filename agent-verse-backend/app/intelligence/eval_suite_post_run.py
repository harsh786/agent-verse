"""What happens once a durable eval-suite run completes (run by exactly one worker)."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


async def on_run_completed(store: Any, run: dict[str, Any], tenant_ctx: Any) -> None:
    """Post-run hook of a completed run (called once, by the finalizing worker)."""
    logger.info(
        "eval_suite_run_finalized",
        run_id=run.get("run_id"),
        suite_id=run.get("suite_id"),
        agent_id=run.get("agent_id"),
    )
