"""Generative-agent read-model repository."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemoryGenerativeRepository(InMemoryPatternStateRepository):
    pass


class PostgresGenerativeRepository(PostgresPatternStateRepository):
    pass


__all__ = ["InMemoryGenerativeRepository", "PostgresGenerativeRepository"]
