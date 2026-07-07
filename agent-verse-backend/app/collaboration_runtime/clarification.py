from __future__ import annotations
import uuid
from dataclasses import dataclass, field


@dataclass
class ClarificationRequest:
    goal_id: str
    ambiguity: str
    options: list[str]
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    requires_pause: bool = True
    human_response: str | None = None


class ClarificationEngine:
    def create_clarification(
        self,
        goal_id: str,
        ambiguity: str,
        options: list[str],
    ) -> ClarificationRequest:
        return ClarificationRequest(
            goal_id=goal_id,
            ambiguity=ambiguity,
            options=options,
            requires_pause=True,
        )
