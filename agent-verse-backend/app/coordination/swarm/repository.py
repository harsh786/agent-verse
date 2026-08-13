"""Swarm read-model repository."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemorySwarmRepository(InMemoryPatternStateRepository):
    pass


class PostgresSwarmRepository(PostgresPatternStateRepository):
    pass


__all__ = ["InMemorySwarmRepository", "PostgresSwarmRepository"]
