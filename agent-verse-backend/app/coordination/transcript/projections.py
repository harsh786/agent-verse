"""Ordered, idempotent read-only projections from the canonical transcript."""

from __future__ import annotations

import inspect
from typing import Any

from app.coordination.transcript.models import TranscriptMessage


class TranscriptProjector:
    def __init__(self, *, blackboard_writer: Any = None, bus_publisher: Any = None) -> None:
        self._blackboard = blackboard_writer
        self._bus = bus_publisher
        self._cursor: dict[tuple[str, str], int] = {}

    @staticmethod
    async def _invoke(callback: Any, **kwargs: Any) -> None:
        result = callback(**kwargs)
        if inspect.isawaitable(result):
            await result

    async def project(self, message: TranscriptMessage) -> bool:
        key = (message.tenant_id, message.session_id)
        current = self._cursor.get(key, 0)
        if message.sequence <= current:
            return False
        if message.sequence != current + 1:
            raise ValueError(
                f"projection sequence gap: expected {current + 1}, found {message.sequence}"
            )
        payload = {
            "message_id": message.message_id,
            "sequence": message.sequence,
            "message_type": message.message_type,
            "safe_content": message.safe_content,
            "artifact_reference": message.artifact_reference,
            "classification": message.classification.value,
            "trust_label": message.trust_label,
            "provenance_chain": list(message.provenance_chain),
        }
        if self._blackboard is not None and message.message_type in {
            "decision",
            "evidence",
            "summary",
        }:
            await self._invoke(
                self._blackboard,
                tenant_id=message.tenant_id,
                session_id=message.session_id,
                payload=payload,
            )
        if self._bus is not None:
            await self._invoke(
                self._bus,
                tenant_id=message.tenant_id,
                session_id=message.session_id,
                payload=payload,
            )
        self._cursor[key] = message.sequence
        return True


__all__ = ["TranscriptProjector"]
