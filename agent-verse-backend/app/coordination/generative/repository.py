"""Generative-agent read-model repository."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemoryGenerativeRepository(InMemoryPatternStateRepository):
    pattern = "generative_agents"


class PostgresGenerativeRepository(PostgresPatternStateRepository):
    pattern = "generative_agents"


__all__ = ["InMemoryGenerativeRepository", "PostgresGenerativeRepository"]
