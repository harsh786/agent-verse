"""Group-chat read-model repository (shared ``strategy_checkpoints``, pattern-scoped)."""

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)


class InMemoryGroupChatRepository(InMemoryPatternStateRepository):
    pattern = "group_chat"


class PostgresGroupChatRepository(PostgresPatternStateRepository):
    pattern = "group_chat"


__all__ = ["InMemoryGroupChatRepository", "PostgresGroupChatRepository"]
