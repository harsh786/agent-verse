"""CAMEL driver: two-role inception-prompted dialogue over the canonical transcript."""

from __future__ import annotations

from typing import Any

from app.coordination.camel.adapter import CamelRuntime
from app.coordination.camel.models import RoleContract
from app.coordination.pattern_runs.context import RunContext, RunOutcome

_SCHEMA = "natural_language_turns_v1"


def _roles(objective: str, names: tuple[str, ...]) -> tuple[RoleContract, ...]:
    instructor, solver = names[0], names[1]
    common: dict[str, Any] = {
        "prohibited_actions": ("calling tools", "acting outside the dialogue"),
        "communication_schema": _SCHEMA,
        "termination_conditions": ("both roles agree the task is complete",),
        "version": 1,
    }
    return (
        RoleContract(
            role_name=instructor,
            objective=f"Guide the solver step by step to complete: {objective[:1_500]}",
            responsibilities=("give one clear instruction per turn", "judge completion"),
            **common,
        ),
        RoleContract(
            role_name=solver,
            objective=f"Carry out the instructor's instructions for: {objective[:1_500]}",
            responsibilities=("execute each instruction", "report concrete solutions"),
            **common,
        ),
    )


async def run_camel(ctx: RunContext) -> RunOutcome:
    roles = _roles(ctx.objective, ctx.participants)
    dialogue: list[str] = []

    async def run_turn(role: RoleContract, progress: dict[str, Any]) -> dict[str, Any]:
        turn = int(progress.get("turn_count", 0)) + 1
        history = "\n".join(dialogue[-6:]) or "(dialogue starts)"
        tokens_before = ctx.llm.tokens
        data = await ctx.llm.json(
            f"Role-play (CAMEL). You are '{role.role_name}'. {role.objective}\n"
            f"Your responsibilities: {', '.join(role.responsibilities)}.\n"
            f"Dialogue so far:\n{history}\n"
            'Write your next turn. Return {"content": str, "completed": bool, '
            '"agreement": bool, "safe_output": str}. Set completed and agreement to true '
            "only when the task is fully solved; then put the final solution in safe_output.",
            step=f"turn-{turn}",
        )
        content = str(data.get("content") or "")[:4_000]
        dialogue.append(f"{role.role_name}: {content}")
        return {
            "content": content,
            "completed": bool(data.get("completed")),
            "agreement": bool(data.get("agreement")),
            "safe_output": str(data.get("safe_output") or ""),
            "requested_tools": (),
            "tokens": ctx.llm.tokens - tokens_before,
        }

    async def authorize_tools(_role_name: str, requested: frozenset[str]) -> bool:
        return not requested  # no role holds tool authority in a pattern run

    state = await CamelRuntime(
        checkpoint_store=ctx.checkpoints, transcript_service=ctx.transcript
    ).execute(
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        execution_id=ctx.execution_id,
        roles=roles,
        available_tools=frozenset(),
        available_connectors=frozenset(),
        platform_authority=frozenset(),
        run_turn=run_turn,
        authorize_tools=authorize_tools,
        maximum_turns=ctx.max_rounds * 2,
        maximum_tokens=int(ctx.options.get("max_tokens", 40_000)),
        maximum_cost_usd=float(ctx.options.get("max_cost_usd", 1.0)),
        deadline=ctx.deadline,
    )
    return RunOutcome(
        phase=state.phase,
        terminal_reason=state.terminal_reason,
        safe_output=state.safe_output,
        view={
            "roles": [role.role_name for role in roles],
            "turn_count": state.turn_count,
            "contract_digest": state.contract_digest,
        },
    )


__all__ = ["run_camel"]
