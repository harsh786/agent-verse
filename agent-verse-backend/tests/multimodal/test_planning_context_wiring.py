"""D-23: multimodal pipeline output must reach the planner prompt.

Before this wiring, extracted spans from an ingested asset were dead-ended
at the /multimodal/ingest API response -- nothing carried them into the
agent's planning context. ``agent_state.context["multimodal_context"]`` is
the hook (populated by app.api.goals._extract_multimodal_context from goal
attachments); this test pins down that _node_plan actually reads it and
includes it in the prompt sent to the planner LLM -- mirroring the existing
(and already-tested) rag_knowledge / image_context injection points.
"""

from __future__ import annotations

from app.agent.graph import AgentGraph
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-mm-plan", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _messages_text(request) -> str:
    parts = []
    for m in request.messages:
        if isinstance(m.content, str):
            parts.append(m.content)
        else:
            for block in m.content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
    return "\n".join(parts)


async def test_multimodal_context_is_injected_into_planner_prompt() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["summarize the attached report"]}',
            "Report summarized",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    await g.run(
        goal="summarize the attached report",
        tenant_ctx=T,
        initial_context={
            "multimodal_context": (
                "[pdf attachment] Q3 revenue grew 12% year over year."
            )
        },
    )

    assert p.call_history, "planner should have been called at least once"
    planner_request = p.call_history[0]
    prompt_text = _messages_text(planner_request)
    assert "[Multimodal asset context]" in prompt_text
    assert "Q3 revenue grew 12%" in prompt_text


async def test_absent_multimodal_context_omits_the_section() -> None:
    """No attachments -> no dead placeholder text in the prompt."""
    p = FakeProvider(
        responses=[
            '{"steps": ["do the thing"]}',
            "done",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    await g.run(goal="do the thing", tenant_ctx=T)

    planner_request = p.call_history[0]
    prompt_text = _messages_text(planner_request)
    assert "[Multimodal asset context]" not in prompt_text
