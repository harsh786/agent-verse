"""Goal / agent helpers for the complex goal, eval and governance scenarios."""

from __future__ import annotations

import os
from typing import Any

from tests.real_world.helpers import LiveAPI, mask, tag, wait_until

GOAL_TIMEOUT = float(os.getenv("RW_GOAL_TIMEOUT", "480"))
TERMINAL = {"complete", "failed", "cancelled", "rejected"}
WAITING = {"waiting_human", "waiting_approval"}


def create_agent(api: LiveAPI, cleanup: Any, prefix: str, **fields: Any) -> str:
    body = {"name": f"{prefix}-{tag()}", "max_iterations": 8, "timeout_seconds": 420, **fields}
    created = api.json_ok("POST", "/agents", json=body)
    agent_id = str(created.get("agent_id") or created.get("id"))
    cleanup("DELETE", f"/agents/{agent_id}")
    return agent_id


def submit(api: LiveAPI, cleanup: Any, goal: str, **fields: Any) -> dict[str, Any]:
    resp = api.post("/goals", json={"goal": goal, **fields})
    assert resp.status_code in (200, 201, 202), (
        f"POST /goals -> {resp.status_code}: {mask(resp.text[:400])}"
    )
    body = dict(resp.json())
    gid = str(body.get("goal_id") or body.get("id") or "")
    assert gid, f"goal submission returned no id: {mask(body)[:300]}"
    cleanup("POST", f"/goals/{gid}/cancel")
    body["goal_id"] = gid
    return body


def get(api: LiveAPI, goal_id: str) -> dict[str, Any]:
    return dict(api.json_ok("GET", f"/goals/{goal_id}"))


def wait_terminal(api: LiveAPI, goal_id: str, timeout: float = GOAL_TIMEOUT) -> dict[str, Any]:
    return dict(wait_until(lambda: get(api, goal_id), timeout=timeout, interval=5,
                           desc=f"goal {goal_id} to finish",
                           done=lambda g: g.get("status") in TERMINAL))


def timeline(api: LiveAPI, goal_id: str) -> list[dict[str, Any]]:
    resp = api.get(f"/goals/{goal_id}/timeline")
    return list(resp.json()) if resp.status_code == 200 else []


def event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("type")) for e in events]


def answer_text(goal: dict[str, Any]) -> str:
    art = goal.get("result_artifact") or {}
    parts = [goal.get("result"), goal.get("final_answer"), art.get("summary"),
             art.get("body"), art.get("markdown"), mask(art.get("tables") or "")]
    return "\n".join(str(p) for p in parts if p and str(p) not in ("None", '""'))


def pending_approvals(api: LiveAPI, goal_id: str) -> list[dict[str, Any]]:
    return [a for a in api.json_ok("GET", "/governance/approvals") or []
            if a.get("goal_id") == goal_id and a.get("status", "pending") == "pending"]


def wait_gate_or_end(api: LiveAPI, goal_id: str, timeout: float = GOAL_TIMEOUT
                     ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Until an approval is pending for the goal or the goal is terminal."""
    result = wait_until(lambda: (get(api, goal_id), pending_approvals(api, goal_id)),
                        timeout=timeout, interval=4, desc=f"approval or end of goal {goal_id}",
                        done=lambda gp: bool(gp[1]) or gp[0].get("status") in TERMINAL)
    return result[0], result[1]


def audit(api: LiveAPI, goal_id: str) -> list[dict[str, Any]]:
    resp = api.get("/governance/audit", params={"goal_id": goal_id, "limit": 500})
    return list(resp.json()) if resp.status_code == 200 else []


def goal_cost(api: LiveAPI, goal: dict[str, Any], events: list[dict[str, Any]]
              ) -> tuple[float | None, str]:
    """The goal's recorded LLM cost and where it was found (None = recorded nowhere)."""
    art = goal.get("result_artifact") or {}
    for label, value in (("goal.cost_usd", goal.get("cost_usd")),
                         ("goal.total_cost_usd", goal.get("total_cost_usd")),
                         ("artifact.cost_usd", art.get("cost_usd")),
                         ("artifact.evidence.cost_usd", (art.get("evidence") or {}).get(
                             "cost_usd"))):
        if isinstance(value, int | float) and value > 0:
            return float(value), label
    rows = audit(api, str(goal.get("goal_id")))
    from_audit = sum(float(r.get("cost_usd") or 0) for r in rows)
    if from_audit > 0:
        return from_audit, "audit.cost_usd"
    from_events = sum(float((e.get("data") or {}).get("cost_usd") or 0) for e in events)
    if from_events > 0:
        return from_events, "timeline.cost_usd"
    return None, "not recorded"
