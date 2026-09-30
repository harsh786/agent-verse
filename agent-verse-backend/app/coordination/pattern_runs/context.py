"""Shared run context and outcome for coordination pattern drivers."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.coordination.contracts import Classification
from app.coordination.pattern_runs.llm import PatternLLM
from app.coordination.pattern_runs.records import RunCheckpointStore, RunDocument

logger = structlog.get_logger(__name__)

Publisher = Callable[[dict[str, Any]], Awaitable[None]]


class PublishingTranscript:
    """Transcript proxy that fans each appended message out on the live bus."""

    def __init__(self, inner: Any, publish: Publisher) -> None:
        self._inner = inner
        self._publish = publish

    async def append(self, **kwargs: Any) -> Any:
        message = await self._inner.append(**kwargs)
        with contextlib.suppress(Exception):
            await self._publish({"type": "message", "message": message.model_dump(mode="json")})
        return message

    async def page(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.page(*args, **kwargs)


@dataclass
class RunContext:
    tenant_id: str
    session_id: str
    execution_id: str
    objective: str
    participants: tuple[str, ...]
    llm: PatternLLM
    document: RunDocument
    transcript: PublishingTranscript
    publish: Publisher
    deadline: datetime
    max_rounds: int
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def checkpoints(self) -> RunCheckpointStore:
        return RunCheckpointStore(self.document)

    async def say(
        self,
        sender: str,
        content: str,
        *,
        step: str,
        message_type: str = "message",
        recipients: tuple[str, ...] = (),
    ) -> None:
        """Record one pattern step in the canonical transcript (durable + live)."""
        await self.transcript.append(
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            sender_agent_id=sender,
            recipient_agent_ids=recipients,
            message_type=message_type,
            content=content[:16_000] or "(empty)",
            classification=Classification.INTERNAL,
            idempotency_key=f"{self.execution_id}:{step}",
            provenance_chain=(f"pattern-run:{self.execution_id}",),
        )

    async def update_view(self, **parts: Any) -> None:
        await self.document.update(view={**self.document.view, **parts})


@dataclass(frozen=True)
class RunOutcome:
    phase: str
    terminal_reason: str | None = None
    safe_output: str | None = None
    view: dict[str, Any] = field(default_factory=dict)


__all__ = ["PublishingTranscript", "RunContext", "RunOutcome"]
