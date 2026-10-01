"""File HITL approval requests that are durable before anyone waits on them.

The sync ``HITLGateway.request_approval`` persists its row fire-and-forget: when
the write fails the request lives in one process's memory, no other replica can
list or approve it, and the caller waits out its whole timeout (or a goal parks
in ``waiting_human`` forever). Every agent-side gate files through here instead.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.governance.hitl import HITLDeliveryError

__all__ = ["HITLDeliveryError", "file_persisted_approval"]


async def file_persisted_approval(
    gateway: Any,
    *,
    goal_id: str,
    action: str,
    risk_level: str,
    tenant_ctx: Any,
) -> str:
    """Create an approval request and return its id once it is persisted.

    Raises:
        HITLDeliveryError: the request could not be made durable; it was dropped,
            so the caller must fail closed (deny the action) rather than wait.
    """
    durable = getattr(gateway, "request_approval_async", None)
    if durable is not None and inspect.iscoroutinefunction(durable):
        return str(
            await durable(
                goal_id=goal_id,
                action=action,
                risk_level=risk_level,
                tenant_ctx=tenant_ctx,
                require_persisted=True,
            )
        )
    # A gateway without the async API (test doubles, in-memory gateways) has no
    # separate durable store to fall behind.
    return str(
        gateway.request_approval(
            goal_id=goal_id, action=action, risk_level=risk_level, tenant_ctx=tenant_ctx
        )
    )
