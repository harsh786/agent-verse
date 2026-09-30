"""CAMEL read-model repository."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemoryCamelRepository(InMemoryPatternStateRepository):
    pattern = "camel"


class PostgresCamelRepository(PostgresPatternStateRepository):
    pattern = "camel"


__all__ = ["InMemoryCamelRepository", "PostgresCamelRepository"]
