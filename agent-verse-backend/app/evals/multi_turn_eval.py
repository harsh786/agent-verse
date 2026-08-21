"""Multi-turn dialogue evaluator.

Evaluates agents on multi-turn conversations where context carries across
turns. Scores: coherence, goal achievement, consistency, and relevance.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiTurnCase:
    name: str
    turns: list[Turn]  # the full expected conversation flow
    expected_final: str  # what the assistant's last turn should achieve
    eval_criteria: list[str] = field(
        default_factory=list
    )  # e.g. ["mentions Python", "asks clarifying question"]


@dataclass
class TurnScore:
    turn_index: int
    coherence: float  # 0-1: is this turn coherent with the conversation so far?
    relevance: float  # 0-1: does this turn address the user's question?
    content: str


@dataclass
class MultiTurnResult:
    case_name: str
    turns_completed: int
    expected_turns: int
    coherence_score: float  # average across all assistant turns
    goal_achieved: bool
    criteria_met: list[str]
    criteria_failed: list[str]
    per_turn_scores: list[TurnScore]
    overall_score: float


class MultiTurnEvaluator:
    """Evaluate agent behaviour across multi-turn conversations.

    Parameters
    ----------
    provider : LLMProvider
        LLM provider used as the judge.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def evaluate(
        self,
        case: MultiTurnCase,
        agent_fn: Callable[[list[Turn]], Coroutine[Any, Any, str]],
    ) -> MultiTurnResult:
        """Run the case against *agent_fn* and score the conversation.

        Parameters
        ----------
        case : MultiTurnCase
            The multi-turn scenario definition.
        agent_fn : Coroutine callable
            An async function that takes the current conversation history
            (list of Turns) and returns the assistant's next reply.
        """
        history: list[Turn] = []
        per_turn_scores: list[TurnScore] = []
        turns_completed = 0

        # Extract user turns from the case to drive the conversation
        user_turns = [t for t in case.turns if t.role == "user"]

        for i, user_turn in enumerate(user_turns):
            history.append(user_turn)
            try:
                assistant_reply = await agent_fn(list(history))
                assistant_turn = Turn(role="assistant", content=assistant_reply)
                history.append(assistant_turn)
                turns_completed += 1

                # Score this assistant turn
                turn_score = await self._score_turn(
                    history=history[:-1],
                    turn=assistant_turn,
                    turn_index=i,
                )
                per_turn_scores.append(turn_score)
            except Exception:
                per_turn_scores.append(
                    TurnScore(turn_index=i, coherence=0.0, relevance=0.0, content="")
                )

        # Judge overall goal achievement
        final_reply = history[-1].content if history and history[-1].role == "assistant" else ""
        goal_achieved, criteria_met, criteria_failed = await self._judge_goal(
            final_reply=final_reply,
            expected=case.expected_final,
            criteria=case.eval_criteria,
        )

        coherence = (
            sum(s.coherence for s in per_turn_scores) / len(per_turn_scores)
            if per_turn_scores
            else 0.0
        )
        goal_score = 1.0 if goal_achieved else 0.0
        criteria_score = len(criteria_met) / max(len(case.eval_criteria), 1)
        overall = round((coherence * 0.4 + goal_score * 0.4 + criteria_score * 0.2), 3)

        return MultiTurnResult(
            case_name=case.name,
            turns_completed=turns_completed,
            expected_turns=len(user_turns),
            coherence_score=round(coherence, 3),
            goal_achieved=goal_achieved,
            criteria_met=criteria_met,
            criteria_failed=criteria_failed,
            per_turn_scores=per_turn_scores,
            overall_score=overall,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _score_turn(
        self,
        history: list[Turn],
        turn: Turn,
        turn_index: int,
    ) -> TurnScore:
        try:
            from app.providers.base import CompletionRequest, Message

            history_text = "\n".join(f"{t.role.upper()}: {t.content[:200]}" for t in history[-4:])
            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Rate the ASSISTANT turn for COHERENCE (0-10) and RELEVANCE (0-10). "
                            'Return only JSON: {"coherence": 8, "relevance": 7}.\n\n'
                            f"Conversation:\n{history_text}\n\n"
                            f"ASSISTANT turn to rate: {turn.content[:300]}"
                        ),
                    )
                ],
                model="",
                max_tokens=40,
                temperature=0.0,
            )
            resp = await self._provider.complete(req)
            import json as _json

            data = _json.loads((resp.content or "{}").strip())
            coh = min(10.0, float(data.get("coherence", 5))) / 10.0
            rel = min(10.0, float(data.get("relevance", 5))) / 10.0
            return TurnScore(
                turn_index=turn_index, coherence=coh, relevance=rel, content=turn.content
            )
        except Exception:
            return TurnScore(
                turn_index=turn_index, coherence=0.5, relevance=0.5, content=turn.content
            )

    async def _judge_goal(
        self,
        final_reply: str,
        expected: str,
        criteria: list[str],
    ) -> tuple[bool, list[str], list[str]]:
        met: list[str] = []
        failed: list[str] = []
        for criterion in criteria:
            if criterion.lower() in final_reply.lower():
                met.append(criterion)
            else:
                failed.append(criterion)
        # Simple goal achievement: check if expected phrases appear
        goal_achieved = bool(
            expected
            and any(phrase.lower() in final_reply.lower() for phrase in expected.split()[:5])
        )
        return goal_achieved, met, failed
