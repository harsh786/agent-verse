"""Persisted run documents for coordination pattern runs.

A run document lives in the pattern's (RLS-scoped, pattern-discriminated)
``strategy_checkpoints`` read model and has three parts:

* ``config`` — the admitted run request (objective, participants, limits);
* ``checkpoint`` — the pattern runtime's latest checkpointed state (the runtimes'
  ``checkpoint_store`` protocol writes here), so a retried run resumes mid-flight
  on any replica;
* ``view`` — the pattern-specific read model the API serves (topology, bids, ...).

Every write is a new immutable version guarded by compare-and-set.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.coordination.state_repository import PatternRecord


class _Snapshot:
    """Duck-typed checkpoint: runtimes call ``model_dump()`` and re-validate it."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return dict(self._data)


class RunDocument:
    def __init__(
        self,
        repository: Any,
        *,
        tenant_id: str,
        session_id: str,
        execution_id: str,
    ) -> None:
        self._repository = repository
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.execution_id = execution_id
        self._version = 0
        self.data: dict[str, Any] = {}

    @property
    def exists(self) -> bool:
        return self._version > 0

    @property
    def config(self) -> dict[str, Any]:
        return dict(self.data.get("config") or {})

    @property
    def checkpoint(self) -> dict[str, Any] | None:
        value = self.data.get("checkpoint")
        return dict(value) if isinstance(value, dict) else None

    @property
    def view(self) -> dict[str, Any]:
        return dict(self.data.get("view") or {})

    async def load(self) -> RunDocument:
        record = await self._repository.get(self.tenant_id, self.session_id, self.execution_id)
        if record is not None:
            self._version = record.version
            self.data = dict(record.state)
        return self

    async def create(self, config: dict[str, Any]) -> None:
        self.data = {"config": config, "checkpoint": None, "view": {}}
        await self._persist()

    async def update(self, **parts: Any) -> None:
        self.data.update(parts)
        await self._persist()

    async def _persist(self) -> None:
        version = self._version + 1
        saved = await self._repository.save(
            PatternRecord(
                tenant_id=self.tenant_id,
                session_id=self.session_id,
                execution_id=self.execution_id,
                # JSON round trip: the in-memory store behaves like Postgres (no
                # shared mutable references, only JSON-safe values persisted).
                state=json.loads(json.dumps(self.data, default=str)),
                version=version,
                idempotency_key=f"{self.session_id}:{self.execution_id}:v{version}",
            ),
            expected_version=self._version,
        )
        self._version = saved.version
        self.data = json.loads(json.dumps(saved.state, default=str))

    # ── checkpoint-store protocol used by the pattern runtimes ──────────────
    async def save(self, state: BaseModel) -> None:
        await self.update(checkpoint=state.model_dump(mode="json"))

    async def load_checkpoint(self) -> _Snapshot | None:
        checkpoint = self.checkpoint
        return _Snapshot(checkpoint) if checkpoint is not None else None


class RunCheckpointStore:
    """Adapter exposing ``save(state)`` / ``load(session, execution)`` for one run."""

    def __init__(self, document: RunDocument) -> None:
        self._document = document

    async def save(self, state: BaseModel) -> None:
        await self._document.save(state)

    async def load(self, session_id: str, execution_id: str) -> _Snapshot | None:
        if (session_id, execution_id) != (
            self._document.session_id,
            self._document.execution_id,
        ):
            return None
        return await self._document.load_checkpoint()


__all__ = ["RunCheckpointStore", "RunDocument"]
