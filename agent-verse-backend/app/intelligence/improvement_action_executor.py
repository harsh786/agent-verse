"""Idempotent bounded execution for all governed improvement action types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.memory.contracts import ImprovementActionRecord

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ImprovementActionExecutor:
    def __init__(self, *, handlers: dict[str, Handler], maximum_attempts: int = 3) -> None:
        self._handlers = handlers
        self._maximum_attempts = maximum_attempts
        self._records: dict[tuple[str, str], ImprovementActionRecord] = {}

    async def execute(
        self, record: ImprovementActionRecord, *, policy_allowed: bool
    ) -> ImprovementActionRecord:
        key = (record.tenant_id, record.idempotency_key)
        prior = self._records.get(key)
        if prior is not None and prior.state in {"completed", "failed", "cancelled"}:
            return prior
        if not policy_allowed:
            denied = record.model_copy(
                update={
                    "state": "failed",
                    "error_code": "policy_denied",
                    "completed_at": datetime.now(UTC),
                }
            )
            self._records[key] = denied
            return denied
        handler = self._handlers.get(record.action_type)
        if handler is None:
            raise RuntimeError(f"missing improvement handler: {record.action_type}")
        if not record.payload:
            failed = record.model_copy(
                update={
                    "state": "failed",
                    "error_code": "no_op_payload",
                    "completed_at": datetime.now(UTC),
                }
            )
            self._records[key] = failed
            return failed
        attempts = record.attempts
        while attempts < self._maximum_attempts:
            attempts += 1
            try:
                result = await handler(record.payload)
                if not result:
                    raise RuntimeError("handler produced no durable result")
                completed = record.model_copy(
                    update={
                        "state": "completed",
                        "attempts": attempts,
                        "result": result,
                        "completed_at": datetime.now(UTC),
                    }
                )
                self._records[key] = completed
                return completed
            except Exception as exc:
                if attempts >= self._maximum_attempts:
                    failed = record.model_copy(
                        update={
                            "state": "failed",
                            "attempts": attempts,
                            "error_code": type(exc).__name__,
                            "completed_at": datetime.now(UTC),
                        }
                    )
                    self._records[key] = failed
                    return failed
        raise AssertionError("unreachable improvement action state")


__all__ = ["ImprovementActionExecutor"]
