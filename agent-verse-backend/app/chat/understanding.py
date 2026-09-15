"""Message understanding — decompose a chat message into discrete actions.

A single message can carry several intents ("schedule X every Monday AND remember
my Q3 date is Sept 20 AND what's the weather?"). This module splits it into an
ordered list of typed actions so each is executed independently.

LLM-first: when a language model is wired, it decomposes + structures the request
(robust to phrasing). A deterministic clause-splitter is the fallback (and the
degraded no-key path), so behaviour is always sensible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.chat.intent import parse_schedule


@dataclass
class ScheduleAction:
    task: str
    cron: str
    human: str
    once: bool = False
    fire_at_iso: str = ""
    type: str = "schedule"


@dataclass
class RememberAction:
    fact: str
    type: str = "remember"


@dataclass
class GoalAction:
    goal: str
    type: str = "goal"


@dataclass
class QAAction:
    question: str
    type: str = "qa"


Action = ScheduleAction | RememberAction | GoalAction | QAAction

# ── Cues ──────────────────────────────────────────────────────────────────────
_REMEMBER = re.compile(
    r"\b(remember|note that|keep in mind|don'?t forget|make a note|for the record)\b", re.I
)
_SCHEDULE_CUE = re.compile(
    r"\b(every|daily|weekly|hourly|each\s+(day|week|morning|evening)|remind me"
    r"|at\s+\d{1,2}([:.]\d{2})?\s*(am|pm)?\b|on\s+\w+\s+\d{1,2}\b|schedule)\b",
    re.I,
)
_GOAL_VERB = re.compile(
    r"^\s*(deploy|run|execute|create|build|fix|delete|update|send|generate|summari[sz]e"
    r"|find|search|post|scan|analy[sz]e|draft|make|schedule)\b",
    re.I,
)
# Split on compound connectors, but not inside common phrases.
_SPLIT = re.compile(r"\s*(?:;|,?\s+and\s+also\s+|,?\s+and\s+|\s+also\s+|\s+plus\s+)\s*", re.I)


def _clean_task(clause: str) -> str:
    """Strip schedule lead-ins from a clause to get the underlying task."""
    t = clause.strip()
    t = re.sub(r"^\s*(please\s+)?(remind me to|remind me|schedule)\s+", "", t, flags=re.I)
    # remove a trailing/leading schedule expression
    t = re.sub(
        r"\b(every\s+\w+|daily|weekly|hourly|each\s+\w+)\b.*?(at\s+\d{1,2}[:.]?\d{0,2}\s*(am|pm)?)?",
        "", t, flags=re.I,
    )
    t = re.sub(r"\bat\s+\d{1,2}([:.]\d{2})?\s*(am|pm)?\b", "", t, flags=re.I)
    t = re.sub(r"\bon\s+(\w+\s+\d{1,2}|\d{1,2}\s+\w+)(st|nd|rd|th)?\b", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.")
    return t or clause.strip()


def _classify_clause(clause: str) -> Action:
    c = clause.strip()
    if not c:
        return QAAction(question=c)
    if _REMEMBER.search(c):
        fact = _REMEMBER.sub("", c, count=1).strip(" ,.:")
        fact = re.sub(r"^\s*(that|to)\s+", "", fact, flags=re.I).strip()
        return RememberAction(fact=fact or c)
    if _SCHEDULE_CUE.search(c):
        p = parse_schedule(c)
        return ScheduleAction(
            task=_clean_task(c), cron=p.cron, human=p.human,
            once=p.once, fire_at_iso=p.fire_at_iso,
        )
    if _GOAL_VERB.search(c):
        return GoalAction(goal=c)
    return QAAction(question=c)


def _fallback_decompose(message: str) -> list[Action]:
    parts = [p for p in _SPLIT.split(message) if p and p.strip()]
    if len(parts) <= 1:
        return [_classify_clause(message)]
    return [_classify_clause(p) for p in parts]


async def decompose(message: str, *, llm: Any = None) -> list[Action]:
    """Decompose *message* into an ordered list of actions (LLM-first, regex fallback)."""
    if llm is not None:
        actions = await _llm_decompose(message, llm)
        if actions:
            return actions
    return _fallback_decompose(message)


_LLM_SYSTEM = (
    "You split a user request into a JSON array of actions. Each action is one of:\n"
    '  {"type":"schedule","task":<what to do>,"human":<when, plain english>}\n'
    '  {"type":"remember","fact":<the fact to store>}\n'
    '  {"type":"goal","goal":<the task to run now>}\n'
    '  {"type":"qa","question":<the question>}\n'
    "Split compound requests (joined by 'and', 'also', ';') into multiple actions. "
    "Output ONLY the JSON array, no prose."
)


async def _llm_decompose(message: str, llm: Any) -> list[Action] | None:
    from app.providers.base import CompletionRequest, Message

    try:
        resp = await llm.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=_LLM_SYSTEM),
                    Message(role="user", content=message),
                ],
                model="", max_tokens=400, temperature=0.0,
            )
        )
        raw = (getattr(resp, "content", "") or "").strip()
    except Exception:
        return None
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        items = json.loads(raw[start : end + 1])
    except Exception:
        return None
    actions: list[Action] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        t = str(it.get("type", "")).lower()
        if t == "schedule":
            task = str(it.get("task") or message)
            p = parse_schedule(str(it.get("human") or it.get("when") or message))
            actions.append(ScheduleAction(
                task=task, cron=str(it.get("cron") or p.cron),
                human=str(it.get("human") or p.human), once=p.once, fire_at_iso=p.fire_at_iso,
            ))
        elif t == "remember" and it.get("fact"):
            actions.append(RememberAction(fact=str(it["fact"])))
        elif t == "goal" and it.get("goal"):
            actions.append(GoalAction(goal=str(it["goal"])))
        elif t == "qa" and it.get("question"):
            actions.append(QAAction(question=str(it["question"])))
    return actions or None
