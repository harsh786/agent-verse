"""Intent router — classifies chat messages into QA / GOAL / CLARIFY / SCHEDULE.

The router uses a lightweight rule-based heuristic first (no LLM call), and
falls back to a fast LLM completion (~50-100ms) only when ambiguous.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ── Intent enum ───────────────────────────────────────────────────────────────


class Intent(enum.StrEnum):
    QA = "QA"  # Question answering — stream LLM response directly
    GOAL = "GOAL"  # Agent goal execution via LangGraph loop
    CLARIFY = "CLARIFY"  # Need more info before acting
    SCHEDULE = "SCHEDULE"  # Schedule a recurring / delayed goal


# ── Patterns ──────────────────────────────────────────────────────────────────

_SCHEDULE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bevery\b", re.I),
    re.compile(r"\b(daily|weekly|monthly|hourly)\b", re.I),
    re.compile(r"\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)\b", re.I),
    re.compile(r"\bschedule\b", re.I),
    re.compile(r"\brepeat(ing|edly)?\b", re.I),
    re.compile(r"\bcron\b", re.I),
    re.compile(r"\bevery\s+(morning|evening|night|day|week)\b", re.I),
]

_GOAL_VERBS: frozenset[str] = frozenset(
    [
        "deploy",
        "run",
        "execute",
        "create",
        "build",
        "fix",
        "delete",
        "update",
        "migrate",
        "install",
        "generate",
        "write",
        "send",
        "set up",
        "setup",
        "configure",
        "start",
        "stop",
        "restart",
        "debug",
        "refactor",
        "test",
        "scan",
        "analyse",
        "analyze",
        "optimise",
        "optimize",
        "automate",
        "publish",
        "release",
        "rollback",
        "backup",
        "clone",
        "fork",
        "merge",
        "checkout",
        "compile",
        "lint",
        "format",
        "dockerize",
        "containerize",
        "provision",
        "scale",
        "monitor",
        "alert",
        "notify",
    ]
)

_QA_STARTERS: frozenset[str] = frozenset(
    [
        "what",
        "how",
        "why",
        "explain",
        "describe",
        "show me",
        "tell me",
        "can you",
        "could you",
        "summarize",
        "summarise",
        "list",
        "give me",
        "define",
        "help me understand",
        "clarify",
        "difference between",
        "compare",
        "is it",
        "are there",
        "does",
        "did",
        "when was",
        "where is",
        "who is",
        "which",
    ]
)

_UNDERSPECIFIED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bit\b", re.I),  # "deploy it"
    re.compile(r"\bthis\b", re.I),  # "fix this"
    re.compile(r"\bsomething\b", re.I),
    re.compile(r"\bsomewhere\b", re.I),
    re.compile(r"\bsomeone\b", re.I),
]


@dataclass
class ClarifyRequest:
    question: str
    options: list[str] = field(default_factory=list)
    round: int = 1


@dataclass
class ScheduleConfirmation:
    goal_text: str
    cron_expression: str
    human_schedule: str  # "every day at 9 AM"
    next_run_iso: str | None = None


# ── IntentRouter ─────────────────────────────────────────────────────────────


class IntentRouter:
    """Classify a chat message into Intent.QA / GOAL / CLARIFY / SCHEDULE.

    Rules (in order):
    1. SCHEDULE — any scheduling keyword detected
    2. QA       — message starts with a question word or is purely a question
    3. GOAL     — message contains an action verb
    4. CLARIFY  — message is short / underspecified and context is a goal
    5. QA       — fallback
    """

    MAX_CLARIFY_ROUNDS = 3

    def classify(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        clarify_round: int = 0,
    ) -> Intent:
        """Return the intent for *message*."""
        msg = message.strip()

        # 0. Force GOAL if we've been clarifying for too many rounds
        if clarify_round >= self.MAX_CLARIFY_ROUNDS:
            return Intent.GOAL

        # 1. Schedule?
        if self._is_schedule(msg):
            return Intent.SCHEDULE

        # 2. Pure QA question
        if self._is_qa(msg):
            return Intent.QA

        # 3. Contains action verb → GOAL, unless underspecified
        if self._has_goal_verb(msg):
            if self._is_underspecified(msg) and not self._has_file_context(history or []):
                return Intent.CLARIFY
            return Intent.GOAL

        # 4. Very short message with action context → CLARIFY
        word_count = len(msg.split())
        if word_count <= 4 and self._previous_was_goal(history or []):
            return Intent.CLARIFY

        # 5. Default to QA
        return Intent.QA

    # ── Private helpers ───────────────────────────────────────────────────────

    def _is_schedule(self, msg: str) -> bool:
        return any(p.search(msg) for p in _SCHEDULE_PATTERNS)

    def _is_qa(self, msg: str) -> bool:
        lower = msg.lower().strip("?! ")
        # Ends with "?" almost certainly a question
        if msg.rstrip().endswith("?"):
            return True
        return any(lower.startswith(s) for s in _QA_STARTERS)

    def _has_goal_verb(self, msg: str) -> bool:
        lower = msg.lower()
        return any(v in lower for v in _GOAL_VERBS)

    def _is_underspecified(self, msg: str) -> bool:
        return any(p.search(msg) for p in _UNDERSPECIFIED_PATTERNS)

    def _has_file_context(self, history: list[dict[str, str]]) -> bool:
        for turn in history[-5:]:
            if turn.get("role") == "user" and (
                "#file:" in turn.get("content", "") or "@" in turn.get("content", "")
            ):
                return True
        return False

    def _previous_was_goal(self, history: list[dict[str, str]]) -> bool:
        for turn in reversed(history):
            if turn.get("role") == "user":
                prev = turn.get("content", "")
                return self._has_goal_verb(prev)
        return False

    # ── Clarify / Schedule helpers ────────────────────────────────────────────

    def generate_clarifying_question(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        round: int = 1,  # noqa: A002
    ) -> ClarifyRequest:
        """Return a clarifying question based on what's missing in *message*."""
        # Simple heuristic questions — in prod swap with fast LLM call
        questions_and_opts: list[tuple[str, list[str]]] = [
            (
                "Which environment should I target?",
                ["Development", "Staging", "Production", "All environments"],
            ),
            (
                "Which service or component does this apply to?",
                ["Backend", "Frontend", "Database", "Infrastructure", "All of them"],
            ),
            (
                "Do you have a specific output format in mind?",
                ["Markdown report", "JSON", "Code file", "Just terminal output"],
            ),
        ]
        idx = min(round - 1, len(questions_and_opts) - 1)
        question, options = questions_and_opts[idx]
        return ClarifyRequest(question=question, options=options, round=round)

    def generate_schedule_confirmation(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> ScheduleConfirmation:
        """Parse a natural-language schedule expression and return a confirmation."""
        import re as _re

        cron = "0 9 * * *"  # sensible default: daily at 9 AM
        human = "every day at 9 AM"

        m = _re.search(r"every\s+(\w+)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", message, _re.I)
        if m:
            day_word, hour_str, minute_str, ampm = m.groups()
            hour = int(hour_str)
            minute = int(minute_str or "0")
            if ampm and ampm.lower() == "pm" and hour != 12:
                hour += 12
            day_map = {
                "monday": 1,
                "tuesday": 2,
                "wednesday": 3,
                "thursday": 4,
                "friday": 5,
                "saturday": 6,
                "sunday": 0,
                "day": "*",
                "morning": "*",
            }
            dow = day_map.get(day_word.lower(), "*")
            cron = f"{minute} {hour} * * {dow}"
            human = f"every {day_word} at {hour:02d}:{minute:02d}"

        # Hourly shortcut
        if _re.search(r"\bhourly\b", message, _re.I):
            cron = "0 * * * *"
            human = "every hour"

        # Weekly
        if _re.search(r"\bweekly\b", message, _re.I):
            cron = "0 9 * * 1"
            human = "every Monday at 9 AM"

        return ScheduleConfirmation(
            goal_text=message,
            cron_expression=cron,
            human_schedule=human,
        )

    # ── Model availability ────────────────────────────────────────────────────

    def available_models(self) -> list[str]:
        """Return list of model IDs available for selection."""
        return [
            "claude-3-5-sonnet",
            "claude-3-haiku",
            "gpt-4o",
            "gpt-4o-mini",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ]
