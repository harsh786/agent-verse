"""Integration endpoints for Slack, Zapier, and email triggers."""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _get_slack_tenant_id() -> str:
    return os.getenv("SLACK_TENANT_ID", "")


def _get_zapier_tenant_id() -> str:
    return os.getenv("ZAPIER_TENANT_ID", "")


# ── Slack ──────────────────────────────────────────────────────────────────────


def _require_slack_signature(
    body: bytes, timestamp: str, signature: str, *, bad_status: int = 403
) -> None:
    """Fail-closed Slack request authentication for the public /integrations routes.

    These routes are in TenantMiddleware's bypass list, so the Slack signature is
    their only authentication. Unconfigured → 503 (in every environment — the old
    development fallback accepted unsigned requests that submit goals / resolve
    approvals for SLACK_TENANT_ID); bad or stale signature → ``bad_status``.
    """
    from app.integrations.slack.handler import (
        get_slack_signing_secret,
        verify_slack_signature,
    )

    secret = get_slack_signing_secret()
    if not secret:
        raise HTTPException(503, "Slack integration is not configured (no signing secret)")
    if not verify_slack_signature(body, timestamp, signature, secret):
        raise HTTPException(bad_status, "Invalid Slack signature")



@router.post("/slack/commands")
async def slack_slash_command(
    request: Request,
    x_slack_signature: str = Header(default=""),
    x_slack_request_timestamp: str = Header(default=""),
) -> Any:
    """Handle /agentverse Slack slash command."""
    body = await request.body()

    _require_slack_signature(body, x_slack_request_timestamp, x_slack_signature)

    params = dict(urllib.parse.parse_qsl(body.decode()))

    text = params.get("text", "").strip()
    user_id = params.get("user_id", "unknown")

    if not text:
        return {
            "response_type": "ephemeral",
            "text": "Usage: /agentverse [your goal description]",
        }

    goal_service = getattr(request.app.state, "goal_service", None)
    if goal_service is None:
        return {"response_type": "ephemeral", "text": "AgentVerse service unavailable"}

    from app.tenancy.context import PlanTier, TenantContext

    slack_tenant_id = _get_slack_tenant_id()
    if not slack_tenant_id:
        # Try legacy settings attribute before failing
        slack_tenant_id = (
            request.app.state.settings.slack_tenant_id
            if hasattr(request.app.state.settings, "slack_tenant_id")
            else ""
        )
    if not slack_tenant_id:
        return {
            "response_type": "ephemeral",
            "text": "⚠️ Slack integration not configured. Ask admin to set SLACK_TENANT_ID env var.",
        }

    ctx = TenantContext(
        tenant_id=slack_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id=f"slack:{user_id}",
    )

    try:
        result = await goal_service.submit_goal(
            goal=text,
            priority="normal",
            dry_run=False,
            tenant_ctx=ctx,
        )
        return {
            "response_type": "in_channel",
            "text": (f"Goal submitted! *{text[:100]}*\nGoal ID: `{result['goal_id']}`"),
        }
    except Exception as exc:
        return {"response_type": "ephemeral", "text": f"Error: {exc}"}


@router.post("/slack/events")
async def slack_events(
    request: Request,
    x_slack_signature: str = Header(default=""),
    x_slack_request_timestamp: str = Header(default=""),
) -> Any:
    """Handle Slack event callbacks (interactive buttons, etc.)."""
    body = await request.body()

    _require_slack_signature(body, x_slack_request_timestamp, x_slack_signature)

    try:
        data = json.loads(body)
    except Exception:
        params = dict(urllib.parse.parse_qsl(body.decode()))
        data = json.loads(params.get("payload", "{}"))

    # Handle URL verification challenge
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # Handle interactive button presses (HITL approve/reject)
    if data.get("type") == "block_actions":
        for action in data.get("actions", []):
            action_id = action.get("action_id")
            request_id = action.get("value", "")
            if not request_id:
                continue

            hitl = getattr(request.app.state, "hitl_gateway", None)
            if hitl:
                from app.tenancy.context import PlanTier, TenantContext

                ctx = TenantContext(
                    tenant_id=_get_slack_tenant_id() or "slack-events",
                    plan=PlanTier.PROFESSIONAL,
                    api_key_id="slack-button",
                )
                approver = data.get("user", {}).get("name", "slack-user")

                if action_id == "approve_hitl":
                    hitl.approve(request_id, approver=approver, tenant_ctx=ctx)
                elif action_id == "reject_hitl":
                    await hitl.reject(request_id, approver=approver, tenant_ctx=ctx)

    return {"ok": True}


@router.post("/slack/interactive")
async def slack_interactive_callback(request: Request) -> dict:
    """Receive Slack interactive component payloads (button clicks for HITL)."""
    import json
    from urllib.parse import unquote_plus

    # Slack sends payload as form-encoded
    body = await request.body()

    _require_slack_signature(
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
        bad_status=401,
    )

    # Parse the payload
    try:
        # Slack sends: payload=<url-encoded-json>
        body_str = body.decode("utf-8")
        if body_str.startswith("payload="):
            payload_json = unquote_plus(body_str[len("payload=") :])
        else:
            payload_json = body_str
        payload = json.loads(payload_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {exc}") from exc

    payload_type = payload.get("type")
    if payload_type != "block_actions":
        return {"ok": True}  # ignore non-button payloads

    # Process each action
    goal_service = getattr(request.app.state, "goal_service", None)

    for action in payload.get("actions", []):
        action_id = action.get("action_id", "")
        value = action.get("value", "")  # goal_id encoded in value
        user = payload.get("user", {}).get("name", "unknown")

        if action_id in ("approve_hitl", "reject_hitl") and value:
            approved = action_id == "approve_hitl"
            feedback = f"{'Approved' if approved else 'Rejected'} by {user} via Slack"

            # Resolve tenant context from the goal
            if goal_service:
                try:
                    from app.tenancy.context import PlanTier, TenantContext

                    tenant_id = os.getenv("SLACK_TENANT_ID", "")
                    if tenant_id:
                        tenant_ctx = TenantContext(
                            tenant_id=tenant_id,
                            plan=PlanTier.PROFESSIONAL,
                            api_key_id="slack-interactive",
                        )
                        await goal_service.resume_goal(
                            goal_id=value,
                            approved=approved,
                            feedback=feedback,
                            tenant_ctx=tenant_ctx,
                        )
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).warning("slack_interactive_resume_failed: %s", exc)

    # Acknowledge immediately (Slack requires response within 3s)
    return {"ok": True}


# ── Zapier ─────────────────────────────────────────────────────────────────────


class ZapierTriggerRequest(BaseModel):
    goal: str | None = None
    text: str | None = None
    message: str | None = None
    agent_id: str | None = None
    priority: str = "normal"
    metadata: dict[str, Any] = {}


@router.post("/zapier/trigger")
async def zapier_trigger(
    request: Request,
    body: ZapierTriggerRequest,
    x_zapier_secret: str = Header(default=""),
) -> dict[str, Any]:
    """Zapier sends data → create AgentVerse goal."""
    from app.integrations.zapier.handler import verify_zapier_secret

    if not verify_zapier_secret(x_zapier_secret):
        raise HTTPException(403, "Invalid Zapier secret")

    goal_text = body.goal or body.text or body.message
    if not goal_text:
        raise HTTPException(422, "No goal text found. Set 'goal', 'text', or 'message'")

    goal_service = getattr(request.app.state, "goal_service", None)
    if not goal_service:
        raise HTTPException(503, "Goal service unavailable")

    from app.tenancy.context import PlanTier, TenantContext

    zapier_tenant_id = _get_zapier_tenant_id()
    if not zapier_tenant_id:
        raise HTTPException(503, "Zapier integration not configured. Set ZAPIER_TENANT_ID env var.")

    ctx = TenantContext(
        tenant_id=zapier_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id="zapier",
    )

    result = await goal_service.submit_goal(
        goal=goal_text,
        priority=body.priority,
        dry_run=False,
        tenant_ctx=ctx,
        agent_id=body.agent_id,
    )

    return {
        "goal_id": result["goal_id"],
        "status": result.get("status", "planning"),
        "goal": goal_text[:200],
        "message": "Goal submitted successfully",
        "track_url": f"/goals/{result['goal_id']}",
    }


# ── Alertmanager Event Bus ─────────────────────────────────────────────────────


class AlertmanagerPayload(BaseModel):
    # field names mirror the Alertmanager webhook JSON schema verbatim (external API contract)
    alerts: list[dict] = []
    groupLabels: dict = {}  # noqa: N815
    commonAnnotations: dict = {}  # noqa: N815
    externalURL: str = ""  # noqa: N815
    status: str = "firing"


@router.post("/events/alertmanager")
async def receive_alertmanager_event(
    request: Request,
    payload: AlertmanagerPayload,
) -> dict:
    """Receive Alertmanager webhook and create goals for firing alerts.

    Authenticated with a bearer token (Alertmanager ``http_config.authorization``)
    matching ALERTMANAGER_WEBHOOK_TOKEN. It had no authentication at all: anyone
    could submit autonomous agent goals into the configured tenant.
    """
    from app.integrations.webhook_auth import require_bearer_token

    require_bearer_token(
        request.headers.get("Authorization", ""),
        env_var="ALERTMANAGER_WEBHOOK_TOKEN",
        label="Alertmanager",
    )
    goal_service = getattr(request.app.state, "goal_service", None)
    created_goals: list[str] = []

    for alert in payload.alerts:
        if alert.get("status") != "firing":
            continue
        alertname = alert.get("labels", {}).get("alertname", "Unknown Alert")
        severity = alert.get("labels", {}).get("severity", "warning")
        annotations = alert.get("annotations", {})
        summary = annotations.get("summary", alertname)

        goal_text = (
            f"Alert: {alertname} (severity={severity})\n"
            f"Summary: {summary}\n"
            f"Investigate and resolve this {severity} alert."
        )

        if goal_service is not None:
            try:
                from app.tenancy.context import PlanTier, TenantContext

                alert_tenant = os.getenv("ALERTMANAGER_TENANT_ID", "")
                if not alert_tenant:
                    continue
                tenant_ctx = TenantContext(
                    tenant_id=alert_tenant,
                    plan=PlanTier.PROFESSIONAL,
                    api_key_id="alertmanager",
                )
                # priority/dry_run are required by submit_goal; omitting them
                # raised a TypeError (swallowed below) so no goal was ever made.
                result = await goal_service.submit_goal(
                    goal=goal_text,
                    priority="high" if severity == "critical" else "normal",
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                )
                created_goals.append(result.get("goal_id", ""))
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("alertmanager_goal_create_failed: %s", exc)

    return {
        "received": len(payload.alerts),
        "goals_created": len(created_goals),
        "goal_ids": created_goals,
    }


# ── Datadog Event Bus ──────────────────────────────────────────────────────────


class DatadogWebhookPayload(BaseModel):
    title: str = ""
    text: str = ""
    alert_type: str = "info"
    event_type: str = ""
    id: str = ""


@router.post("/events/datadog")
async def receive_datadog_event(
    request: Request,
    payload: DatadogWebhookPayload,
) -> dict:
    """Receive Datadog webhook events and create goals for critical alerts.

    The HMAC signature (DATADOG_WEBHOOK_SECRET) is always required; it used to be
    checked only when the secret happened to be set, i.e. open by default.
    """
    from app.integrations.webhook_auth import require_hmac_body_signature

    require_hmac_body_signature(
        await request.body(),
        request.headers.get("X-Datadog-Signature", ""),
        env_var="DATADOG_WEBHOOK_SECRET",
        label="Datadog",
    )

    if payload.alert_type not in ("error", "critical", "warning"):
        return {
            "status": "ignored",
            "reason": f"alert_type={payload.alert_type} not critical",
        }

    goal_service = getattr(request.app.state, "goal_service", None)
    goal_id: str | None = None
    if goal_service is not None:
        goal_text = f"Datadog Alert: {payload.title}\n{payload.text[:500]}"
        dd_tenant = os.getenv("DATADOG_TENANT_ID", "")
        if dd_tenant:
            try:
                from app.tenancy.context import PlanTier, TenantContext

                ctx = TenantContext(
                    tenant_id=dd_tenant,
                    plan=PlanTier.PROFESSIONAL,
                    api_key_id="datadog",
                )
                # priority/dry_run are required by submit_goal (see alertmanager).
                result = await goal_service.submit_goal(
                    goal=goal_text,
                    priority="normal" if payload.alert_type == "warning" else "high",
                    dry_run=False,
                    tenant_ctx=ctx,
                )
                goal_id = result.get("goal_id")
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("datadog_goal_failed: %s", exc)

    return {
        "status": "processed",
        "goal_id": goal_id,
        "alert_type": payload.alert_type,
    }


@router.get("/zapier/goals")
async def zapier_poll_completed_goals(request: Request) -> list[dict[str, Any]]:
    """Zapier polling trigger — returns recently completed goals.

    Requires the same X-Zapier-Secret as /zapier/trigger; it had none, so anyone
    could read the Zapier tenant's completed goals (goal text and results).
    """
    from app.integrations.zapier.handler import verify_zapier_secret

    if not verify_zapier_secret(request.headers.get("X-Zapier-Secret", "")):
        raise HTTPException(403, "Invalid Zapier secret")
    goal_service = getattr(request.app.state, "goal_service", None)
    if not goal_service:
        return []

    from app.tenancy.context import PlanTier, TenantContext

    zapier_tenant_id = _get_zapier_tenant_id()
    if not zapier_tenant_id:
        return []

    ctx = TenantContext(
        tenant_id=zapier_tenant_id,
        plan=PlanTier.PROFESSIONAL,
        api_key_id="zapier-poll",
    )

    try:
        result = await goal_service.list_goals(tenant_ctx=ctx)
        completed = [g for g in result.get("goals", []) if g.get("status") == "complete"]
        return completed[:10]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Re-ingestion webhooks — trigger delta ingestion from external sources
# ---------------------------------------------------------------------------


async def _authenticated_reingest_target(
    request: Request, signature_header: str
) -> tuple[str, str]:
    """Resolve and AUTHENTICATE (tenant_id, collection_id) for a re-ingest webhook.

    The ids come from the webhook URL (query) or headers, but are only trusted
    once the body carries a valid HMAC signature under that collection's secret
    (``webhook_auth.reingest_signing_secret``) and the collection exists for that
    tenant. These routes were unauthenticated: anyone could queue ingestion of an
    attacker-chosen repository/page into any tenant's collection.
    """
    from app.integrations.webhook_auth import verify_reingest_signature

    collection_id = request.headers.get("X-AgentVerse-Collection-Id") or request.query_params.get(
        "collection_id", ""
    )
    tenant_id = request.headers.get("X-AgentVerse-Tenant-Id") or request.query_params.get(
        "tenant_id", ""
    )
    if not collection_id or not tenant_id:
        raise HTTPException(400, "collection_id and tenant_id are required")
    verify_reingest_signature(
        await request.body(),
        request.headers.get(signature_header, ""),
        tenant_id=tenant_id,
        collection_id=collection_id,
    )
    store = getattr(request.app.state, "knowledge_store", None)
    if store is not None:
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="reingest-webhook")
        if await store.get_collection_async(collection_id, tenant_ctx=ctx) is None:
            raise HTTPException(404, "Collection not found")
    return tenant_id, collection_id


@router.post("/webhooks/github/push")
async def github_push_webhook(request: Request) -> dict[str, Any]:
    """Handle GitHub push events and queue re-ingestion of changed files.

    Expects a GitHub webhook payload (push event). The tenant and collection
    are resolved from the X-AgentVerse-Collection-Id header or query param.
    """
    tenant_id, collection_id = await _authenticated_reingest_target(request, "X-Hub-Signature-256")
    payload = await request.json()

    # Queue a delta re-ingest Celery task
    try:
        from app.scaling.tasks import delta_reingest_files

        repo = payload.get("repository", {})
        result = delta_reingest_files.delay(
            tenant_id=tenant_id,
            collection_id=collection_id,
            source_type="github",
            source_config={
                "repo": repo.get("full_name", ""),
                "ref": payload.get("ref", ""),
                "commits": payload.get("commits", []),
            },
        )
        return {"status": "queued", "task_id": result.id, "source": "github"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/webhooks/confluence/page-updated")
async def confluence_page_webhook(request: Request) -> dict[str, Any]:
    """Handle Confluence page_updated events and queue re-ingestion."""
    tenant_id, collection_id = await _authenticated_reingest_target(
        request, "X-AgentVerse-Signature"
    )
    payload = await request.json()

    page_id = payload.get("page", {}).get("id", "")
    space_key = payload.get("space", {}).get("key", "")
    try:
        from app.scaling.tasks import delta_reingest_files

        result = delta_reingest_files.delay(
            tenant_id=tenant_id,
            collection_id=collection_id,
            source_type="confluence",
            source_config={"page_id": page_id, "space_key": space_key},
        )
        return {"status": "queued", "task_id": result.id, "source": "confluence"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/webhooks/notion/page-updated")
async def notion_page_webhook(request: Request) -> dict[str, Any]:
    """Handle Notion webhook events and queue re-ingestion of updated pages."""
    tenant_id, collection_id = await _authenticated_reingest_target(
        request, "X-AgentVerse-Signature"
    )
    payload = await request.json()

    page_id = payload.get("entity", {}).get("id", "") or payload.get("page_id", "")
    try:
        from app.scaling.tasks import delta_reingest_files

        result = delta_reingest_files.delay(
            tenant_id=tenant_id,
            collection_id=collection_id,
            source_type="notion",
            source_config={"page_id": page_id},
        )
        return {"status": "queued", "task_id": result.id, "source": "notion"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
