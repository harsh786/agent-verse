"""CAMEL read-model repository."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemoryCamelRepository(InMemoryPatternStateRepository):
    pass


class PostgresCamelRepository(PostgresPatternStateRepository):
    pass


__all__ = ["InMemoryCamelRepository", "PostgresCamelRepository"]
