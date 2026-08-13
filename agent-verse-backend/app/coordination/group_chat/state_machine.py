from enum import StrEnum

from app.coordination.state_machines import InvalidTransitionError


class GroupChatState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    AWAITING_HUMAN = "awaiting_human"
    COMPACTING = "compacting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TRANSITIONS = {
    GroupChatState.CREATED: frozenset({GroupChatState.ACTIVE, GroupChatState.CANCELLED}),
    GroupChatState.ACTIVE: frozenset(
        {
            GroupChatState.AWAITING_HUMAN,
            GroupChatState.COMPACTING,
            GroupChatState.COMPLETED,
            GroupChatState.FAILED,
            GroupChatState.CANCELLED,
        }
    ),
    GroupChatState.AWAITING_HUMAN: frozenset(
        {GroupChatState.ACTIVE, GroupChatState.CANCELLED, GroupChatState.FAILED}
    ),
    GroupChatState.COMPACTING: frozenset(
        {GroupChatState.ACTIVE, GroupChatState.FAILED, GroupChatState.CANCELLED}
    ),
    GroupChatState.COMPLETED: frozenset(),
    GroupChatState.FAILED: frozenset(),
    GroupChatState.CANCELLED: frozenset(),
}


def transition_group_chat(current: GroupChatState, target: GroupChatState) -> GroupChatState:
    if target not in TRANSITIONS[current]:
        raise InvalidTransitionError(f"invalid group chat transition: {current} -> {target}")
    return target


__all__ = ["TRANSITIONS", "GroupChatState", "transition_group_chat"]
