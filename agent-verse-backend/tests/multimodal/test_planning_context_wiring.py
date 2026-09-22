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


# ── Visual context staleness (no expiry/freshness mechanism) ────────────────
#
# FINDING (not a fix): `agent_state.context["image_context"]` (planner_mixin.py
# line ~190) is a plain string set once, from a single screenshot taken before
# planning starts (see app/api/perception.py `submit_goal_with_image` /
# app/perception/multimodal.py). Nothing in app/agent/state.py or
# app/agent/graph.py ever clears, timestamps, or re-validates it. Replanning
# (verify -> replan -> plan) re-enters the *same* `_node_plan` function
# against the *same* `agent_state.context` dict, so the identical
# `[Visual context]` block from the original screenshot -- taken before any
# execution happened -- is re-injected verbatim into every subsequent
# planning call, even after intervening steps (e.g. an RPA navigation) have
# made that screenshot stale. There is no expiry timestamp, no "page URL
# changed since this screenshot was taken" check, and no mechanism for a
# step's own execution result (e.g. a fresh `rpa_screenshot`) to invalidate
# or replace the original `image_context`. This test pins down that current
# (lack of) behavior; it is a documented gap, not something this change
# silently "fixes" -- see the task report for the recommendation.


async def test_stale_visual_context_is_still_reinjected_unchanged_on_replan() -> None:
    """A screenshot taken before planning is re-sent to the planner on every
    replan iteration, identical and unmarked as potentially stale -- even
    though a step executed in between (in a real RPA run, that step could
    have navigated the page the screenshot was taken from)."""
    original_screenshot_context = (
        "[Visual context: screenshot of https://example.com/checkout-step-1] "
        "Page shows a shopping cart with 2 items."
    )
    p = FakeProvider(
        responses=[
            '{"steps": ["click checkout"]}',  # plan #1
            "clicked checkout, page navigated to step 2",  # execute #1
            '{"success": false, "reason": "not done yet"}',  # verify #1 -> replan
            '{"steps": ["fill shipping form"]}',  # plan #2 (replan)
            "filled shipping form",  # execute #2
            '{"success": true, "reason": "done"}',  # verify #2 -> complete
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    state = await g.run(
        goal="complete checkout",
        tenant_ctx=T,
        initial_context={"image_context": original_screenshot_context},
    )

    assert state.iterations == 2, "goal must have gone through a replan cycle"
    # call_history: [plan1, execute1, verify1, plan2(replan), execute2, verify2]
    assert len(p.call_history) == 6
    first_plan_prompt = _messages_text(p.call_history[0])
    second_plan_prompt = _messages_text(p.call_history[3])

    for prompt_text in (first_plan_prompt, second_plan_prompt):
        assert "[Visual context]" in prompt_text
        assert original_screenshot_context in prompt_text

    # The replanned prompt carries the byte-identical visual context block as
    # the very first plan -- nothing marks it as potentially outdated even
    # though a full step (with its own navigation-changing side effects in a
    # real RPA run) executed in between. This is the staleness gap: no
    # timestamp, no "still valid for this step?" check, no invalidation hook.
    def _visual_block(text: str) -> str:
        marker = "[Visual context]"
        start = text.index(marker)
        return text[start : start + len(marker) + 1 + len(original_screenshot_context)]

    assert _visual_block(first_plan_prompt) == _visual_block(second_plan_prompt)
