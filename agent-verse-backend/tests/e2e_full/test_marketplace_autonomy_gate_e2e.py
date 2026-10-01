"""e2e_full (MARKETPLACE-EVAL-GATE): installing a fully-autonomous template.

MEM-22 lets an agent become fully-autonomous only through the rollout gate.
Template installs bypassed it (the template's mode went straight into
``agents``). Against the real app + Postgres: the installed agent row is
bounded-autonomous, the response says why, and upgrading it with PUT
/agents/{id} on a never-run eval suite is refused by the rollout gate (409).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_fully_autonomous_template_installs_bounded_and_gate_still_applies(
    tenant_client: Any,
) -> None:
    published = await tenant_client.post(
        "/marketplace/templates",
        json={
            "name": f"Nightly autopilot {uuid.uuid4().hex[:6]}",
            "slug": f"autopilot-{uuid.uuid4().hex[:8]}",
            "description": "Summarise yesterday's tickets",
            "template_config": {
                "goal_template": "Summarise yesterday's support tickets",
                "autonomy_mode": "fully-autonomous",
            },
            "visibility": "private",
        },
    )
    assert published.status_code == 201, published.text
    template_id = published.json().get("template_id") or published.json()["id"]

    deployed = await tenant_client.post(
        f"/marketplace/templates/{template_id}/deploy", json={"params": {}}
    )
    assert deployed.status_code == 200, deployed.text
    body = deployed.json()
    assert body["autonomy_mode"] == "bounded-autonomous"
    assert body["requested_autonomy_mode"] == "fully-autonomous"
    assert body["autonomy_downgraded"] is True

    agent = await tenant_client.get(f"/agents/{body['agent_id']}")
    assert agent.status_code == 200, agent.text
    assert agent.json()["autonomy_mode"] == "bounded-autonomous"

    upgrade = await tenant_client.put(
        f"/agents/{body['agent_id']}",
        json={"autonomy_mode": "fully-autonomous", "eval_suite_id": uuid.uuid4().hex},
    )
    assert upgrade.status_code == 409, upgrade.text
    assert upgrade.json()["detail"]["code"] == "ROLLOUT_GATE_FAILED"
