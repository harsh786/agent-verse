from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class MissingInputRequest:
    goal_id: str
    missing_item: str
    reason: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    requires_pause: bool = True


class MissingInputEngine:
    def create(
        self,
        goal_id: str,
        missing_item: str,
        reason: str,
    ) -> MissingInputRequest:
        return MissingInputRequest(
            goal_id=goal_id,
            missing_item=missing_item,
            reason=reason,
        )
