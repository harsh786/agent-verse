"""Gap-free sequence-cursor replay independent of Redis availability."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class InvalidCursorError(ValueError):
    """The requested sequence cursor or page size is invalid."""


class UnsupportedSchemaVersionError(ValueError):
    """An event cannot be safely upcast to the current schema."""


class ReplayEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    tenant_id: str
    session_id: str
    sequence: int = Field(gt=0)
    event_id: str
    schema_version: int = 1
    event_type: str
    payload: dict[str, Any] | None = None


class ReplayRepository(Protocol):
    async def page(
        self,
        *,
        tenant_id: str,
        session_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[Any]: ...


class SequenceReplay:
    """Read canonical events using exclusive monotonic sequence cursors."""

    current_schema_version = 1

    def __init__(
        self,
        repository: ReplayRepository,
        *,
        max_page_size: int = 500,
        upcasters: Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    ) -> None:
        self._repository = repository
        self._max_page_size = max_page_size
        self._upcasters = dict(upcasters or {})

    async def page(
        self,
        *,
        tenant_id: str,
        session_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[ReplayEvent]:
        if after_sequence < 0:
            raise InvalidCursorError("after_sequence cannot be negative")
        if limit <= 0 or limit > self._max_page_size:
            raise InvalidCursorError("page size exceeds the configured bound")
        rows = await self._repository.page(
            tenant_id=tenant_id,
            session_id=session_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        normalized = sorted(
            (ReplayEvent.model_validate(self._event_data(row)) for row in rows),
            key=lambda event: event.sequence,
        )
        seen: set[str] = set()
        result: list[ReplayEvent] = []
        for event in normalized:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            result.append(self._upcast(event))
        return result

    async def all(
        self,
        *,
        tenant_id: str,
        session_id: str,
        page_size: int | None = None,
        after_sequence: int = 0,
    ) -> list[ReplayEvent]:
        size = page_size or self._max_page_size
        cursor = after_sequence
        events: list[ReplayEvent] = []
        seen: set[str] = set()
        while True:
            page = await self.page(
                tenant_id=tenant_id,
                session_id=session_id,
                after_sequence=cursor,
                limit=size,
            )
            if not page:
                return events
            for event in page:
                cursor = max(cursor, event.sequence)
                if event.event_id not in seen:
                    seen.add(event.event_id)
                    events.append(event)

    @staticmethod
    def _event_data(row: Any) -> dict[str, Any]:
        if isinstance(row, BaseModel):
            return row.model_dump()
        if isinstance(row, dict):
            return row
        return {
            "tenant_id": row.tenant_id,
            "session_id": row.session_id,
            "sequence": row.sequence,
            "event_id": row.event_id,
            "schema_version": row.schema_version,
            "event_type": row.event_type,
            "payload": row.payload,
        }

    def _upcast(self, event: ReplayEvent) -> ReplayEvent:
        if event.schema_version == self.current_schema_version:
            return event
        if event.schema_version > self.current_schema_version:
            raise UnsupportedSchemaVersionError(
                f"unsupported event schema version: {event.schema_version}"
            )
        payload = dict(event.payload or {})
        version = event.schema_version
        while version < self.current_schema_version:
            upcaster = self._upcasters.get(version)
            if upcaster is None:
                raise UnsupportedSchemaVersionError(
                    f"no upcaster for event schema version: {version}"
                )
            payload = upcaster(payload)
            version += 1
        return event.model_copy(
            update={"schema_version": self.current_schema_version, "payload": payload}
        )


__all__ = [
    "InvalidCursorError",
    "ReplayEvent",
    "ReplayRepository",
    "SequenceReplay",
    "UnsupportedSchemaVersionError",
]
