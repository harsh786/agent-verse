from __future__ import annotations
import uuid
from dataclasses import dataclass, field


@dataclass
class PreferenceOption:
    key: str
    description: str


@dataclass
class PreferenceSession:
    goal_id: str
    question: str
    options: list[PreferenceOption]
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    selected: str | None = None


class PreferenceCapture:
    def create(
        self,
        goal_id: str,
        question: str,
        options: list[PreferenceOption],
    ) -> PreferenceSession:
        return PreferenceSession(goal_id=goal_id, question=question, options=options)
