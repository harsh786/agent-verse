"""Magentic run records (checkpointed runtime state + run configuration)."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemoryMagenticRunRepository(InMemoryPatternStateRepository):
    pattern = "magentic"


class PostgresMagenticRunRepository(PostgresPatternStateRepository):
    pattern = "magentic"


__all__ = ["InMemoryMagenticRunRepository", "PostgresMagenticRunRepository"]
