"""Intent router — classifies chat messages into QA / GOAL / CLARIFY / SCHEDULE.

The router uses a lightweight rule-based heuristic first (no LLM call), and
falls back to a fast LLM completion (~50-100ms) only when ambiguous.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def _re_search(pattern: str, text: str) -> re.Match[str] | None:
    """Case-insensitive search helper."""
    return re.search(pattern, text, re.I)


# Month name/abbrev → number (includes the common "sept" abbreviation).
_MONTHS: dict[str, int] = {}
_MONTH_NAMES: dict[int, str] = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}
for _i, (_full, _abbr) in enumerate(
    [
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
        ("may", "may"), ("june", "jun"), ("july", "jul"), ("august", "aug"),
        ("september", "sep"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ],
    start=1,
):
    _MONTHS[_full] = _i
    _MONTHS[_abbr] = _i
_MONTHS["sept"] = 9  # common alt abbreviation

_DOW: dict[str, int] = {
    "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
    "thursday": 4, "friday": 5, "saturday": 6,
}
_DOW_NAMES: dict[int, str] = {v: k.capitalize() for k, v in _DOW.items()}

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

    async def classify_async(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        clarify_round: int = 0,
        llm: Any = None,
    ) -> Intent:
        """Regex-first classification with a fast-LLM fallback for ambiguous input.

        The regex ``classify`` stays the fast path. The LLM is consulted ONLY when
        the message fell through to the default QA bucket with no positive signal
        (and an ``llm`` is provided) — so clear QA/GOAL/SCHEDULE never pay the
        latency. Any LLM/parse failure keeps the regex result (never crashes).
        """
        regex_intent = self.classify(message, history, clarify_round)
        if llm is None or not self._is_ambiguous(message, history or [], clarify_round):
            return regex_intent
        llm_intent = await self._llm_disambiguate(message, llm)
        return llm_intent or regex_intent

    def _is_ambiguous(
        self, message: str, history: list[dict[str, str]], clarify_round: int
    ) -> bool:
        """True when the regex path would fall through to its default QA bucket."""
        msg = message.strip()
        if clarify_round >= self.MAX_CLARIFY_ROUNDS:
            return False
        if self._is_schedule(msg) or self._is_qa(msg) or self._has_goal_verb(msg):
            return False
        return not (len(msg.split()) <= 4 and self._previous_was_goal(history))

    async def _llm_disambiguate(self, message: str, llm: Any) -> Intent | None:
        import json
        import re

        from app.providers.base import CompletionRequest, Message

        system = (
            "You classify a user's chat message intent for an AI assistant. "
            'Respond ONLY with JSON: {"intent": "qa"|"goal"|"schedule"}. '
            "qa = answer a question or chat; goal = perform a task using tools; "
            "schedule = set up a recurring or future action. No other text."
        )
        try:
            resp = await llm.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=system),
                        Message(role="user", content=message[:2000]),
                    ],
                    model="",
                    max_tokens=20,
                    temperature=0.0,
                )
            )
            content = getattr(resp, "content", "") or ""
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                return None
            value = str(json.loads(match.group(0)).get("intent", "")).lower()
            return {
                "qa": Intent.QA,
                "goal": Intent.GOAL,
                "schedule": Intent.SCHEDULE,
            }.get(value)
        except Exception:
            return None

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
        """Parse a natural-language schedule expression and return a confirmation.

        Handles time with ``:`` or ``.`` minutes and am/pm ("6.11pm" → 18:11),
        a specific date ("15 sept", "September 20th" → a one-time run on that date),
        a day-of-week ("every Monday"), and hourly/weekly shortcuts.
        """
        hour, minute = self._extract_time(message)
        day, month = self._extract_date(message)
        dow = self._extract_dow(message)

        if _re_search(r"\bhourly\b", message):
            return ScheduleConfirmation(message, "0 * * * *", "every hour")
        if day and month:
            cron = f"{minute} {hour} {day} {month} *"
            human = f"on {_MONTH_NAMES[month]} {day} at {hour:02d}:{minute:02d}"
        elif dow is not None:
            cron = f"{minute} {hour} * * {dow}"
            human = f"every {_DOW_NAMES[dow]} at {hour:02d}:{minute:02d}"
        elif _re_search(r"\bweekly\b", message):
            cron = f"{minute} {hour} * * 1"
            human = f"every Monday at {hour:02d}:{minute:02d}"
        else:
            cron = f"{minute} {hour} * * *"
            human = f"every day at {hour:02d}:{minute:02d}"

        return ScheduleConfirmation(
            goal_text=message, cron_expression=cron, human_schedule=human
        )

    @staticmethod
    def _extract_time(message: str) -> tuple[int, int]:
        """Return (hour_24, minute); defaults to 09:00 when no time is present."""
        # H:MM or H.MM with optional am/pm  (6.11pm, 09:30, 6:11 pm)
        m = _re_search(r"\b(\d{1,2})[:.](\d{2})\s*([ap]m)?\b", message)
        if m:
            hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        else:
            # bare hour with am/pm  (6pm, 9 am)
            m = _re_search(r"\b(\d{1,2})\s*([ap]m)\b", message)
            if not m:
                return 9, 0
            hour, minute, ampm = int(m.group(1)), 0, m.group(2)
        # Only apply am/pm to a 12-hour clock value; a 24-hour hour (>12) already
        # encodes the period, so ignore a contradictory suffix ("18.02pm" → 18:02).
        if ampm and 1 <= hour <= 12:
            ampm = ampm.lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
        return (hour % 24), (minute % 60)

    @staticmethod
    def _extract_date(message: str) -> tuple[int | None, int | None]:
        """Return (day, month) for a specific date like '15 sept' / 'September 20th'."""
        month_alt = "|".join(_MONTHS)
        m = _re_search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_alt})\b", message)
        if m:
            return int(m.group(1)), _MONTHS[m.group(2).lower()]
        m = _re_search(rf"\b({month_alt})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", message)
        if m:
            return int(m.group(2)), _MONTHS[m.group(1).lower()]
        return None, None

    @staticmethod
    def _extract_dow(message: str) -> int | None:
        """Return cron day-of-week (0=Sun..6=Sat) for 'every Monday', else None."""
        m = _re_search(
            r"\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b", message
        )
        return _DOW.get(m.group(1).lower()) if m else None

    # ── Model availability ────────────────────────────────────────────────────

    def available_models(self) -> list[str]:
        """Return model IDs available for selection.

        The system-configured model (NVIDIA/self-hosted/…) is listed first so it
        takes priority; the cloud slugs remain as additional options.
        """
        from app.providers.model_defaults import configured_default_model

        configured = configured_default_model("")
        base = [
            "claude-3-5-sonnet",
            "claude-3-haiku",
            "gpt-4o",
            "gpt-4o-mini",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ]
        if configured and configured not in base:
            return [configured, *base]
        return base
