"""Swarm read-model repository."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemorySwarmRepository(InMemoryPatternStateRepository):
    pattern = "decentralized_swarm"


class PostgresSwarmRepository(PostgresPatternStateRepository):
    pattern = "decentralized_swarm"


__all__ = ["InMemorySwarmRepository", "PostgresSwarmRepository"]
