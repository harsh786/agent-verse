"""B7-L2: a goal that completes is scored as completed.

The verifier scored a successful goal BEFORE setting its status to COMPLETE, so
``task_completion`` (1.0 iff the goal reached COMPLETE) was 0.0 on every
completed worker-run goal (live: every ``evaluations`` row of a completed goal
read ``task_completion: 0.0``) and the overall average lost 1/7. A
goal_score_below trigger on ``task_completion`` fired for every successful goal,
and an overall threshold above ~0.85 fired for all of them.
"""

from __future__ import annotations

from app.agent.graph import AgentGraph
from app.agent.state import GoalStatus
from app.intelligence.eval_runner import EvalRunner
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-b7-score", plan=PlanTier.ENTERPRISE, api_key_id="k")


async def test_completed_goal_scores_full_task_completion() -> None:
    p = FakeProvider(responses=['{"steps": ["s"]}', "o", '{"success": true, "reason": "ok"}'])
    g = AgentGraph(planner=p, executor=p, verifier=p, eval_runner=EvalRunner())

    state = await g.run(goal="score me", tenant_ctx=T)

    assert state.status == GoalStatus.COMPLETE
    scorecard = state.context["eval_scorecard"]
    assert scorecard.scores["task_completion"] == 1.0
