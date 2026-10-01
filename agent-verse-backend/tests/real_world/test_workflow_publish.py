"""WF-PUBLISH-APPROVAL: four-eyes publishing of a workflow.

With ``requires_publish_approval`` a direct publish is refused, the draft is
submitted for approval, the submitter cannot approve their own request, and a
rejection returns it to draft — each step audited. When a second key of the
same tenant is supplied (``RW_APPROVER_API_KEY``) the approval path is run too:
the workflow goes live and a new version is recorded.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, body_of, mask, register_secret


@pytest.mark.scenario("WF-PUBLISH-APPROVAL")
def test_workflow_publish_requires_second_approver(api: LiveAPI, cleanup: Any,
                                                   evidence: dict[str, Any]) -> None:
    wf = wfx.create_workflow(api, cleanup, prefix="rw-publish-gated")
    wid = wf["id"]
    evidence["workflow_id"] = wid
    v1 = f"{wfx.V1}/workflows/{wid}"

    patch = api.patch(v1, json={"requires_publish_approval": True})
    evidence["enable_http"] = patch.status_code
    assert patch.status_code == 200, f"PATCH requires_publish_approval -> {patch.status_code}: " \
        f"{mask(patch.text[:300])}"

    direct = api.post(f"{v1}/publish")
    evidence["direct_publish_http"] = direct.status_code
    assert direct.status_code == 409, f"direct publish not refused: {direct.status_code}"

    sub = api.post(f"{v1}/submit-for-approval")
    evidence["submit_http"] = sub.status_code
    assert sub.status_code == 202, f"submit -> {sub.status_code}: {mask(sub.text[:300])}"
    evidence["status_after_submit"] = body_of(sub).get("status")
    assert body_of(sub).get("status") == "pending_approval", body_of(sub)

    own = api.post(f"{v1}/approve-publish", json={"note": "self approval"})
    evidence["self_approve_http"] = own.status_code
    evidence["self_approve_detail"] = mask(own.text[:200])
    assert own.status_code == 409 and "submitter" in own.text.lower(), (
        f"submitter approved their own publish request: {own.status_code} {mask(own.text[:200])}"
    )

    second = os.getenv("RW_APPROVER_API_KEY", "")
    if second:
        register_secret(second)
        approver = LiveAPI(second)
        try:
            ok = approver.post(f"{v1}/approve-publish", json={"note": "four-eyes ok"})
            evidence["approve_http"] = ok.status_code
            assert ok.status_code == 200, f"approve -> {ok.status_code}: {mask(ok.text[:300])}"
            assert body_of(ok).get("status") == "published", body_of(ok)
            cleanup("POST", f"{v1}/unpublish")
        finally:
            approver.close()
        versions = api.json_ok("GET", f"{v1}/versions")
        items = versions.get("items", versions) if isinstance(versions, dict) else versions
        evidence["versions"] = len(items or [])
        assert items, "publishing recorded no version"
    else:
        rej = api.post(f"{v1}/reject-publish", json={"note": "needs a rollback step"})
        evidence["reject_http"] = rej.status_code
        assert rej.status_code == 200, f"reject -> {rej.status_code}: {mask(rej.text[:300])}"
        evidence["status_after_reject"] = body_of(rej).get("status")
        assert body_of(rej).get("status") == "draft", body_of(rej)

    rows = wfx.audit_rows(api, wid)
    names = sorted({str(r.get("tool_name")) for r in rows})
    evidence["audit_tool_names"] = names
    for expected in ("workflow.created", "workflow.publish_submitted"):
        assert expected in names, f"missing audit row {expected}: {names}"
    assert any(r.get("tool_name") == "workflow.publish_approved" and r.get("outcome") == "denied"
               for r in rows), "the refused self-approval was not audited"
