"""Generative-agents driver: observe -> reflect -> plan/act on a bounded simulation clock."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from app.coordination.generative.adapter import GenerativeAgentRuntime
from app.coordination.generative.models import Observation, Persona
from app.coordination.generative.observation import ObservationStore
from app.coordination.generative.persona import validate_persona
from app.coordination.generative.reflection import ReflectionService
from app.coordination.pattern_runs.context import RunContext, RunOutcome
from app.coordination.pattern_runs.llm import string_list

_REFLECT_EVERY = 3


def _score(value: Any) -> int:
    try:
        return max(0, min(10_000, int(value)))
    except (TypeError, ValueError):
        return 0


async def run_generative(ctx: RunContext) -> RunOutcome:
    persona_id = ctx.participants[0]
    persona = validate_persona(
        Persona(
            persona_id=persona_id,
            tenant_id=ctx.tenant_id,
            version=1,
            public_traits=("methodical", "curious"),
            goals=(ctx.objective[:1_000],),
            relationships=tuple(ctx.participants[1:]),
            behavioral_constraints=("act only within the simulation", "no tool use"),
            memory_namespace=f"generative:{ctx.execution_id}",
            authority_ceiling=frozenset(),
        ),
        platform_authority=frozenset(),
    )
    view = ctx.document.view
    observations: list[dict[str, Any]] = list(view.get("observations") or [])
    reflections: list[dict[str, Any]] = list(view.get("reflections") or [])
    store = ObservationStore()
    for item in observations:  # rehydrate memory on resume
        await store.add(Observation.model_validate(item))
    reflector = ReflectionService(importance_threshold=9_000, minimum_evidence=2)
    start = datetime.fromisoformat(
        str(view.get("simulation_start") or datetime.now(UTC).isoformat())
    )
    await ctx.update_view(
        simulation_start=start.isoformat(), persona=persona.model_dump(mode="json")
    )

    async def run_action(index: int, agent: Persona) -> dict[str, Any]:
        now = start + timedelta(hours=index)
        memory = await store.recall(ctx.tenant_id, agent.persona_id, now=now, limit=5)
        recalled = [item.safe_summary for item in memory]
        lessons = [item["safe_conclusion"] for item in reflections[-3:]]
        data = await ctx.llm.json(
            f"You are generative agent '{agent.persona_id}' (traits: "
            f"{', '.join(agent.public_traits)}). Goal: {agent.goals[0]}\n"
            f"Simulation hour {index}. Recent memories: {recalled}\nReflections: {lessons}\n"
            'Observe, plan and act one step. Return {"observation": str, "importance": '
            '0-10000, "action": str, "completed": bool, "safe_output": str (your answer so far)}',
            step=f"act-{index}",
        )
        summary = str(data.get("observation") or data.get("action") or "")[:2_000]
        if summary:
            observation = await store.add(
                Observation(
                    observation_id=hashlib.sha256(
                        f"{ctx.execution_id}:{index}".encode()
                    ).hexdigest()[:32],
                    tenant_id=ctx.tenant_id,
                    persona_id=agent.persona_id,
                    occurred_at=now,
                    safe_summary=summary,
                    importance=_score(data.get("importance")),
                    relevance=5_000,
                    confidence=5_000,
                    evidence_references=(f"generative://{ctx.execution_id}/{index}",),
                    classification="internal",
                    expires_at=now + timedelta(days=30),
                    idempotency_key=f"{ctx.execution_id}:observation:{index}",
                )
            )
            observations.append(observation.model_dump(mode="json"))
        if (index + 1) % _REFLECT_EVERY == 0:
            recent = await store.recall(ctx.tenant_id, agent.persona_id, now=now, limit=6)
            conclusion = await ctx.llm.text(
                "Reflect: what higher-level insight follows from these observations? "
                f"{[item.safe_summary for item in recent]}\nOne sentence.",
                step=f"reflect-{index}",
                max_tokens=200,
            )
            reflection = reflector.reflect(recent, conclusion=conclusion[:2_000])
            if reflection is not None:
                reflections.append(reflection.model_dump(mode="json"))
        action = str(data.get("action") or "")[:4_000]
        await ctx.say(
            agent.persona_id,
            f"[hour {index}] {action or summary or '(no action)'}",
            step=f"generative:act:{index}",
            message_type="evidence",
        )
        await ctx.update_view(
            observations=observations[-50:],
            reflections=reflections[-20:],
            plan=string_list([action], limit=1),
        )
        return {"completed": bool(data.get("completed")), "safe_output": data.get("safe_output")}

    state = await GenerativeAgentRuntime(checkpoint_store=ctx.checkpoints).execute(
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        execution_id=ctx.execution_id,
        persona=persona,
        start=start,
        step=timedelta(hours=1),
        maximum_events=ctx.max_rounds,
        horizon=timedelta(hours=ctx.max_rounds + 1),
        run_action=run_action,
    )
    return RunOutcome(
        phase=state.phase,
        terminal_reason=state.terminal_reason,
        safe_output=state.safe_output,
        view={
            "event_count": state.event_count,
            "simulation_time": state.simulation_time.isoformat(),
            "observations": observations[-50:],
            "reflections": reflections[-20:],
        },
    )


__all__ = ["run_generative"]
