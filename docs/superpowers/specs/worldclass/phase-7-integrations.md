# Phase 7: Integrations (Slack, Zapier, GitHub Actions, Email, WebSocket Tools)

**Status:** Not started  
**Priority:** High — extends AgentVerse reach into every developer workflow  
**Acceptance gate:** `pytest agent-verse-backend/tests/integrations/ -v` all green; Slack slash command `/agentverse` submits a goal in < 2s; GitHub Action runs in sample repo; email polling loop processes a test message; WebSocket tool streams 10 events.

---

## 1. Current State

| Area | File | Current Behaviour |
|------|------|-------------------|
| Integrations | `agent-verse-backend/app/` | No `integrations/` directory. No Slack, Zapier, GitHub, or email adapters. |
| WebSocket tools | `agent-verse-backend/app/mcp/` | Only HTTP-based `MCPClient`. No WebSocket transport. |
| Inbound webhooks | `agent-verse-backend/app/api/` | No public webhook surface for third-party triggers. |
| Celery Beat tasks | `agent-verse-backend/app/scaling/celery_app.py` | No scheduled email check task. |

---

## 2. Gap Description

Goals today can only be submitted through the web UI or CLI. There is no way for a developer to trigger agent goals from Slack, Zapier, GitHub Actions, or email. This blocks non-technical users and CI/CD automation. The MCP layer also only supports synchronous HTTP tool calls; real-time data connectors (stock prices, IoT sensors) need WebSocket transport.

---

## 3. Full Implementation

### 3.1 Slack App Integration

#### Directory layout

```
agent-verse-backend/app/integrations/slack/
├── __init__.py
├── app.py          # Slack Bolt app
├── router.py       # FastAPI endpoints
└── config.py       # Env-var config
```

#### `app/integrations/slack/config.py`

```python
"""Slack integration configuration."""

from __future__ import annotations

import os


def slack_bot_token() -> str:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN environment variable is not set.")
    return token


def slack_signing_secret() -> str:
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not secret:
        raise RuntimeError("SLACK_SIGNING_SECRET environment variable is not set.")
    return secret


SLACK_GOAL_CHANNEL: str = os.getenv("SLACK_GOAL_CHANNEL", "")
```

#### `app/integrations/slack/app.py`

```python
"""Slack Bolt app — slash command and HITL approval buttons."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from app.integrations.slack.config import slack_bot_token, slack_signing_secret

logger = logging.getLogger(__name__)

_slack_app: AsyncApp | None = None
_handler: AsyncSlackRequestHandler | None = None


def get_slack_app() -> AsyncApp:
    global _slack_app
    if _slack_app is None:
        _slack_app = AsyncApp(
            token=slack_bot_token(),
            signing_secret=slack_signing_secret(),
        )
        _register_handlers(_slack_app)
    return _slack_app


def get_slack_handler() -> AsyncSlackRequestHandler:
    global _handler
    if _handler is None:
        _handler = AsyncSlackRequestHandler(get_slack_app())
    return _handler


def _register_handlers(bolt_app: AsyncApp) -> None:
    """Register all Slack event and action handlers."""

    @bolt_app.command("/agentverse")
    async def handle_slash_command(ack, body, say, client):
        """Handle /agentverse [goal text] slash command."""
        await ack()
        goal_text: str = body.get("text", "").strip()
        user_id: str = body.get("user_id", "unknown")
        channel_id: str = body.get("channel_id", "")

        if not goal_text:
            await say("Usage: `/agentverse <your goal>`\n\nExample: `/agentverse Summarise all open Jira tickets`")
            return

        try:
            goal_id = await _submit_goal_from_slack(goal_text, user_id)
            await say(
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Goal submitted* :rocket:\n> {goal_text}\n\nGoal ID: `{goal_id}`\nStatus: _running..._",
                        },
                    }
                ]
            )
            # Start background task to notify on completion
            asyncio.create_task(
                _notify_on_completion(goal_id, channel_id, goal_text, client)
            )
        except Exception as exc:
            logger.exception("Failed to submit goal from Slack: %s", exc)
            await say(f":x: Failed to submit goal: {exc}")

    @bolt_app.event("message")
    async def handle_dm_message(event, say, client):
        """Handle direct messages as goal submissions."""
        channel_type = event.get("channel_type")
        if channel_type != "im":
            return
        text: str = event.get("text", "").strip()
        if not text or text.startswith("/"):
            return
        user_id: str = event.get("user", "unknown")
        channel: str = event.get("channel", "")

        try:
            goal_id = await _submit_goal_from_slack(text, user_id)
            await say(f"Goal submitted :white_check_mark:\nGoal ID: `{goal_id}`\nI'll notify you when it completes.")
            asyncio.create_task(_notify_on_completion(goal_id, channel, text, client))
        except Exception as exc:
            await say(f":x: Error: {exc}")

    @bolt_app.action("hitl_approve")
    async def handle_approve_action(ack, body, client):
        """Handle HITL approval button click."""
        await ack()
        action_value: str = body["actions"][0].get("value", "")
        user_id: str = body["user"]["id"]
        try:
            payload = json.loads(action_value)
            request_id: str = payload["request_id"]
            await _approve_hitl(request_id, approver=f"slack:{user_id}")
            await client.chat_update(
                channel=body["container"]["channel_id"],
                ts=body["container"]["message_ts"],
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f":white_check_mark: *Approved* by <@{user_id}>"},
                    }
                ],
            )
        except Exception as exc:
            logger.exception("HITL approve failed: %s", exc)

    @bolt_app.action("hitl_reject")
    async def handle_reject_action(ack, body, client):
        """Handle HITL reject button click."""
        await ack()
        action_value: str = body["actions"][0].get("value", "")
        user_id: str = body["user"]["id"]
        try:
            payload = json.loads(action_value)
            request_id: str = payload["request_id"]
            await _reject_hitl(request_id, approver=f"slack:{user_id}")
            await client.chat_update(
                channel=body["container"]["channel_id"],
                ts=body["container"]["message_ts"],
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f":x: *Rejected* by <@{user_id}>"},
                    }
                ],
            )
        except Exception as exc:
            logger.exception("HITL reject failed: %s", exc)


# ---------------------------------------------------------------------------
# Helpers — wire to app services
# ---------------------------------------------------------------------------

async def _submit_goal_from_slack(goal_text: str, user_id: str) -> str:
    """Submit a goal via GoalService. Returns goal_id."""
    from app.main import _app_state  # type: ignore[attr-defined]
    svc = _app_state.goal_service
    tenant = _make_slack_tenant()
    goal_id = await svc.submit_goal(
        goal=goal_text,
        tenant_context=tenant,
        tool_context=None,
        metadata={"source": "slack", "slack_user_id": user_id},
    )
    return goal_id


async def _notify_on_completion(
    goal_id: str, channel: str, goal_text: str, client: Any
) -> None:
    """Poll goal status and send Slack message on terminal state."""
    from app.main import _app_state  # type: ignore[attr-defined]
    import asyncio

    svc = _app_state.goal_service
    deadline = asyncio.get_event_loop().time() + 600  # 10-minute timeout

    while asyncio.get_event_loop().time() < deadline:
        state = svc.get_goal_state(goal_id)
        if state is None:
            break
        from app.agent.state import GoalStatus
        if state.status == GoalStatus.COMPLETED:
            await client.chat_postMessage(
                channel=channel,
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f":white_check_mark: *Goal completed*\n> {goal_text}\n\n"
                                f"*Result:* {(state.result or 'Done')[:500]}"
                            ),
                        },
                    }
                ],
            )
            return
        if state.status == GoalStatus.FAILED:
            await client.chat_postMessage(
                channel=channel,
                text=f":x: Goal failed: {state.error or 'unknown error'}",
            )
            return
        if state.status.value == "waiting_approval":
            await _send_hitl_approval_message(goal_id, state, channel, client)
        await asyncio.sleep(3)


async def _send_hitl_approval_message(
    goal_id: str, state: Any, channel: str, client: Any
) -> None:
    """Send HITL approval request as interactive Slack message."""
    request_id = getattr(state, "pending_approval_id", "unknown")
    action = getattr(state, "pending_approval_action", "an action")
    import json

    await client.chat_postMessage(
        channel=channel,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":bell: *Approval Required*\n> Goal `{goal_id}` wants to: *{action}*",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "hitl_approve",
                        "value": json.dumps({"request_id": request_id, "goal_id": goal_id}),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": "hitl_reject",
                        "value": json.dumps({"request_id": request_id, "goal_id": goal_id}),
                    },
                ],
            },
        ],
    )


async def _approve_hitl(request_id: str, approver: str) -> None:
    from app.main import _app_state  # type: ignore[attr-defined]
    await _app_state.hitl_gateway.approve(request_id, approver=approver, note="Approved via Slack")


async def _reject_hitl(request_id: str, approver: str) -> None:
    from app.main import _app_state  # type: ignore[attr-defined]
    await _app_state.hitl_gateway.reject(request_id, approver=approver, note="Rejected via Slack")


def _make_slack_tenant():  # type: ignore[return]
    from app.tenancy.context import PlanTier, TenantContext
    import os
    return TenantContext(
        tenant_id=os.getenv("SLACK_TENANT_ID", "default"),
        plan_tier=PlanTier.PRO,
        api_key=os.getenv("SLACK_AGENTVERSE_API_KEY", ""),
        settings={},
    )
```

#### `app/integrations/slack/router.py`

```python
"""FastAPI router for Slack event and command endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.integrations.slack.app import get_slack_handler

router = APIRouter(prefix="/integrations/slack", tags=["integrations-slack"])


@router.post("/events")
async def slack_events(req: Request) -> Response:
    """Receive Slack events (event subscriptions webhook)."""
    handler = get_slack_handler()
    return await handler.handle(req)


@router.post("/commands")
async def slack_commands(req: Request) -> Response:
    """Receive Slack slash commands."""
    handler = get_slack_handler()
    return await handler.handle(req)


@router.post("/interactions")
async def slack_interactions(req: Request) -> Response:
    """Receive Slack interactive payloads (button clicks etc.)."""
    handler = get_slack_handler()
    return await handler.handle(req)
```

**Register in `app/main.py`** (add import and router inclusion):

```python
# In create_app() after existing router includes:
from app.integrations.slack.router import router as slack_router
app.include_router(slack_router)
```

**Required pyproject.toml addition:**

```toml
[project.optional-dependencies]
slack = [
    "slack-bolt>=1.21.0",
]
```

---

### 3.2 Zapier Webhook Adapter

#### Directory layout

```
agent-verse-backend/app/integrations/zapier/
├── __init__.py
├── router.py
└── auth.py
```

#### `app/integrations/zapier/auth.py`

```python
"""Zapier secret validation."""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import Header, HTTPException, status


def _zapier_secret() -> str:
    secret = os.getenv("ZAPIER_SECRET", "")
    if not secret:
        raise RuntimeError("ZAPIER_SECRET environment variable is not set.")
    return secret


async def validate_zapier_secret(x_zapier_secret: str = Header(alias="X-Zapier-Secret")) -> str:
    """FastAPI dependency that validates the Zapier shared secret header."""
    expected = _zapier_secret()
    if not hmac.compare_digest(x_zapier_secret.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Zapier secret.",
        )
    return x_zapier_secret
```

#### `app/integrations/zapier/router.py`

```python
"""FastAPI router for Zapier integration endpoints.

Supports:
  - POST /integrations/zapier/trigger — inbound data → create goal
  - GET  /integrations/zapier/poll    — polling trigger for completions
  - POST /integrations/zapier/action/submit-goal — explicit goal submission action
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.integrations.zapier.auth import validate_zapier_secret

router = APIRouter(prefix="/integrations/zapier", tags=["integrations-zapier"])

# In-memory ring-buffer for completed goals (for polling trigger)
# In production this would be a Redis sorted set
_completed_goals: list[dict[str, Any]] = []
_MAX_POLL_BUFFER = 500


# ── Request / Response models ─────────────────────────────────────────────────

class ZapierTriggerPayload(BaseModel):
    """Payload shape Zapier sends to our trigger endpoint."""

    goal: str = Field(..., description="Natural language goal text.")
    priority: str = Field("normal", description="Goal priority: low|normal|high.")
    context: dict[str, Any] = Field(default_factory=dict)
    zapier_hook_id: str | None = None


class ZapierGoalOutput(BaseModel):
    """Shape of completed goal exposed to Zapier output mapping."""

    id: str
    goal: str
    status: str
    result: str | None
    cost_usd: float
    completed_at: str | None


class ZapierSubmitAction(BaseModel):
    goal: str
    priority: str = "normal"
    context: dict[str, Any] = Field(default_factory=dict)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/trigger")
async def zapier_trigger(
    payload: ZapierTriggerPayload,
    _secret: Annotated[str, Depends(validate_zapier_secret)],
    request: Request,
) -> dict[str, Any]:
    """Zapier sends data here → AgentVerse creates a goal.

    Returns the goal_id so Zapier can track it.
    """
    goal_service = request.app.state.goal_service
    tenant = _make_zapier_tenant()

    goal_id = await goal_service.submit_goal(
        goal=payload.goal,
        tenant_context=tenant,
        tool_context=None,
        metadata={
            "source": "zapier",
            "priority": payload.priority,
            "zapier_hook_id": payload.zapier_hook_id,
            **payload.context,
        },
    )
    return {
        "goal_id": goal_id,
        "status": "submitted",
        "message": f"Goal submitted successfully. Track at /goals/{goal_id}",
    }


@router.get("/poll")
async def zapier_poll(
    _secret: Annotated[str, Depends(validate_zapier_secret)],
    since: float | None = None,
) -> list[dict[str, Any]]:
    """Zapier polling trigger — returns recently completed goals.

    Zapier polls this endpoint every ~5 minutes to detect new results.
    Returns a list of ZapierGoalOutput objects (newest first, max 10).
    """
    cutoff = since or (time.time() - 300)  # default: last 5 minutes
    recent = [
        g for g in reversed(_completed_goals)
        if g.get("_completed_ts", 0) >= cutoff
    ]
    return recent[:10]


@router.post("/action/submit-goal")
async def zapier_submit_goal(
    payload: ZapierSubmitAction,
    _secret: Annotated[str, Depends(validate_zapier_secret)],
    request: Request,
) -> dict[str, Any]:
    """Zapier action — submit a goal and optionally wait for result.

    For async Zapier actions, returns goal_id immediately.
    """
    goal_service = request.app.state.goal_service
    tenant = _make_zapier_tenant()

    goal_id = await goal_service.submit_goal(
        goal=payload.goal,
        tenant_context=tenant,
        tool_context=None,
        metadata={"source": "zapier-action", "priority": payload.priority, **payload.context},
    )
    return {
        "goal_id": goal_id,
        "status": "submitted",
        "track_url": f"/goals/{goal_id}",
    }


@router.post("/webhook/goal-complete")
async def receive_goal_completion(
    body: dict[str, Any],
    _secret: Annotated[str, Depends(validate_zapier_secret)],
) -> dict[str, str]:
    """Internal endpoint — GoalService calls this when a goal completes.

    Adds to the poll buffer for Zapier's polling trigger.
    """
    import time

    _completed_goals.append({**body, "_completed_ts": time.time()})
    if len(_completed_goals) > _MAX_POLL_BUFFER:
        _completed_goals.pop(0)
    return {"ok": "true"}


def _make_zapier_tenant():  # type: ignore[return]
    from app.tenancy.context import PlanTier, TenantContext
    import os
    return TenantContext(
        tenant_id=os.getenv("ZAPIER_TENANT_ID", "default"),
        plan_tier=PlanTier.PRO,
        api_key=os.getenv("ZAPIER_AGENTVERSE_API_KEY", ""),
        settings={},
    )
```

**Register in `app/main.py`:**

```python
from app.integrations.zapier.router import router as zapier_router
app.include_router(zapier_router)
```

---

### 3.3 GitHub Actions Integration

#### Directory layout

```
agent-verse-github-action/
├── action.yml
├── Dockerfile
└── entrypoint.sh
```

#### `action.yml`

```yaml
name: "AgentVerse Run Goal"
description: "Submit a goal to AgentVerse and wait for the result in CI."
author: "AgentVerse"

branding:
  icon: "zap"
  color: "purple"

inputs:
  api-key:
    description: "AgentVerse API key (store in GitHub Secrets)."
    required: true
  goal:
    description: "Natural language goal to execute."
    required: true
  base-url:
    description: "AgentVerse API base URL."
    required: false
    default: "https://api.agentverse.ai"
  wait-timeout:
    description: "Maximum seconds to wait for goal completion."
    required: false
    default: "300"
  fail-on-error:
    description: "Fail the workflow step if the goal fails."
    required: false
    default: "true"

outputs:
  goal-id:
    description: "The AgentVerse goal ID."
  status:
    description: "Final goal status: completed | failed | cancelled."
  result:
    description: "Goal result text (truncated to 2000 chars)."
  cost-usd:
    description: "LLM cost incurred by the goal."

runs:
  using: "docker"
  image: "Dockerfile"
  env:
    AGENTVERSE_API_KEY: ${{ inputs.api-key }}
    AGENTVERSE_BASE_URL: ${{ inputs.base-url }}
    AGENTVERSE_GOAL: ${{ inputs.goal }}
    AGENTVERSE_TIMEOUT: ${{ inputs.wait-timeout }}
    AGENTVERSE_FAIL_ON_ERROR: ${{ inputs.fail-on-error }}
```

#### `Dockerfile`

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir httpx

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

#### `entrypoint.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${AGENTVERSE_BASE_URL:-https://api.agentverse.ai}"
API_KEY="${AGENTVERSE_API_KEY}"
GOAL="${AGENTVERSE_GOAL}"
TIMEOUT="${AGENTVERSE_TIMEOUT:-300}"
FAIL_ON_ERROR="${AGENTVERSE_FAIL_ON_ERROR:-true}"

if [[ -z "$API_KEY" ]]; then
  echo "::error::AGENTVERSE_API_KEY is required"
  exit 1
fi

if [[ -z "$GOAL" ]]; then
  echo "::error::goal input is required"
  exit 1
fi

echo "::notice::Submitting goal: ${GOAL}"

# Submit goal
SUBMIT_RESP=$(python3 - <<EOF
import httpx, json, sys
r = httpx.post(
    "${BASE_URL}/goals",
    headers={"X-API-Key": "${API_KEY}", "Content-Type": "application/json"},
    json={"goal": "${GOAL}", "priority": "normal"},
    timeout=30,
)
if r.status_code >= 400:
    print(f"::error::Submit failed {r.status_code}: {r.text}", file=sys.stderr)
    sys.exit(1)
print(r.text)
EOF
)

GOAL_ID=$(echo "$SUBMIT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['goal_id'])")
echo "::notice::Goal ID: ${GOAL_ID}"
echo "goal-id=${GOAL_ID}" >> "${GITHUB_OUTPUT:-/dev/null}"

# Poll for completion
DEADLINE=$((SECONDS + TIMEOUT))
STATUS="pending"
RESULT=""
COST="0.0"

while [[ $SECONDS -lt $DEADLINE ]]; do
  sleep 5
  POLL_RESP=$(python3 - <<EOF
import httpx, json, sys
r = httpx.get(
    "${BASE_URL}/goals/${GOAL_ID}",
    headers={"X-API-Key": "${API_KEY}"},
    timeout=10,
)
if r.status_code >= 400:
    print(f"::warning::Poll failed {r.status_code}", file=sys.stderr)
    sys.exit(0)
print(r.text)
EOF
)
  STATUS=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
  echo "::debug::Status: ${STATUS}"

  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" || "$STATUS" == "cancelled" ]]; then
    RESULT=$(echo "$POLL_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('result') or '')[:2000])" 2>/dev/null || echo "")
    COST=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cost_usd',0.0))" 2>/dev/null || echo "0.0")
    break
  fi
done

# Write outputs
{
  echo "status=${STATUS}"
  echo "result=${RESULT}"
  echo "cost-usd=${COST}"
} >> "${GITHUB_OUTPUT:-/dev/null}"

echo "::notice::Goal ${GOAL_ID} finished with status: ${STATUS}"
echo "::notice::Cost: \$${COST}"

if [[ "$STATUS" == "failed" && "$FAIL_ON_ERROR" == "true" ]]; then
  echo "::error::Goal failed: ${RESULT}"
  exit 1
fi

if [[ "$STATUS" == "pending" || "$STATUS" == "running" ]]; then
  echo "::warning::Goal timed out after ${TIMEOUT}s"
  if [[ "$FAIL_ON_ERROR" == "true" ]]; then
    exit 1
  fi
fi

echo "Done."
```

---

### 3.4 Email-to-Goal (IMAP Inbound)

#### Directory layout

```
agent-verse-backend/app/integrations/email/
├── __init__.py
└── imap_listener.py
```

#### `app/integrations/email/imap_listener.py`

```python
"""Async IMAP email listener — polls for emails and creates AgentVerse goals.

Configuration (environment variables):
  IMAP_HOST           IMAP server hostname (e.g. imap.gmail.com)
  IMAP_PORT           IMAP port (default 993)
  IMAP_USER           IMAP account username
  IMAP_PASSWORD       IMAP account password
  IMAP_MAILBOX        Mailbox to poll (default INBOX)
  IMAP_GOAL_EMAIL     Email address that receives goal submissions
  IMAP_TENANT_ID      Tenant ID for submitted goals
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import logging
import os
from email.message import EmailMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _imap_config() -> dict[str, str | int]:
    return {
        "host": os.environ.get("IMAP_HOST", ""),
        "port": int(os.environ.get("IMAP_PORT", "993")),
        "user": os.environ.get("IMAP_USER", ""),
        "password": os.environ.get("IMAP_PASSWORD", ""),
        "mailbox": os.environ.get("IMAP_MAILBOX", "INBOX"),
        "goal_email": os.environ.get("IMAP_GOAL_EMAIL", ""),
    }


def _is_email_enabled() -> bool:
    cfg = _imap_config()
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


# ---------------------------------------------------------------------------
# Core listener
# ---------------------------------------------------------------------------

class IMAPGoalListener:
    """Polls an IMAP mailbox and converts new emails into AgentVerse goals.

    Uses `aioimaplib` for async IMAP operations.
    """

    def __init__(self, goal_service: object, tenant_context: object) -> None:
        self._goal_service = goal_service
        self._tenant_context = tenant_context
        self._processed_uids: set[bytes] = set()

    async def poll_once(self) -> int:
        """Connect, fetch unseen messages, create goals, reply with goal_id.

        Returns number of goals created.
        """
        if not _is_email_enabled():
            logger.debug("IMAP email integration is not configured; skipping poll.")
            return 0

        try:
            import aioimaplib  # type: ignore[import]
        except ImportError:
            logger.warning("aioimaplib is not installed. Install with: pip install aioimaplib")
            return 0

        cfg = _imap_config()
        goals_created = 0

        try:
            client = aioimaplib.IMAP4_SSL(host=str(cfg["host"]), port=int(cfg["port"]))
            await client.wait_hello_from_server()
            await client.login(str(cfg["user"]), str(cfg["password"]))
            await client.select(str(cfg["mailbox"]))

            # Search for unseen messages
            _status, data = await client.search("UNSEEN")
            if not data or not data[0]:
                await client.logout()
                return 0

            uid_list = data[0].split()
            for uid in uid_list:
                if uid in self._processed_uids:
                    continue
                try:
                    goals_created += await self._process_message(client, uid)
                    self._processed_uids.add(uid)
                except Exception as exc:
                    logger.exception("Failed to process email uid=%s: %s", uid, exc)

            await client.logout()
        except Exception as exc:
            logger.exception("IMAP poll failed: %s", exc)

        return goals_created

    async def _process_message(self, client: object, uid: bytes) -> int:
        """Fetch a single message, parse it, and create a goal."""
        _status, msg_data = await client.fetch(uid.decode(), "(RFC822)")  # type: ignore[attr-defined]
        if not msg_data:
            return 0

        raw_email = msg_data[1]
        msg: EmailMessage = email.message_from_bytes(raw_email, policy=email.policy.default)  # type: ignore[assignment]

        subject: str = str(msg.get("Subject", "")).strip()
        sender: str = str(msg.get("From", "")).strip()
        body: str = self._extract_body(msg)

        if not subject:
            logger.debug("Skipping email with empty subject from %s", sender)
            return 0

        # Build goal text from subject + body
        goal_text = subject
        if body:
            goal_text = f"{subject}\n\nContext:\n{body[:2000]}"

        logger.info("Creating goal from email: subject=%r sender=%r", subject, sender)

        goal_id = await self._goal_service.submit_goal(  # type: ignore[attr-defined]
            goal=goal_text,
            tenant_context=self._tenant_context,
            tool_context=None,
            metadata={"source": "email", "from": sender, "subject": subject},
        )

        # Reply with goal_id
        await self._send_reply(client, msg, goal_id)
        return 1

    def _extract_body(self, msg: EmailMessage) -> str:
        """Extract plaintext body from email message."""
        body_parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body_parts.append(payload.decode("utf-8", errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                body_parts.append(payload.decode("utf-8", errors="replace"))
        return "\n".join(body_parts).strip()

    async def _send_reply(self, client: object, original: EmailMessage, goal_id: str) -> None:
        """Send an auto-reply with the goal_id and stream URL."""
        try:
            import smtplib
            from email.mime.text import MIMEText

            cfg = _imap_config()
            reply_to = str(original.get("From", ""))
            subject = f"Re: {original.get('Subject', '')}"
            stream_url = f"http://localhost:8000/goals/{goal_id}/stream"

            body = (
                f"Your goal has been submitted to AgentVerse.\n\n"
                f"Goal ID: {goal_id}\n"
                f"Stream URL: {stream_url}\n\n"
                f"You will receive a follow-up email when the goal completes.\n"
            )

            mime_msg = MIMEText(body)
            mime_msg["Subject"] = subject
            mime_msg["From"] = str(cfg["user"])
            mime_msg["To"] = reply_to

            # Use SMTP for sending (IMAP is receive-only)
            smtp_host = os.environ.get("SMTP_HOST", str(cfg["host"]).replace("imap.", "smtp."))
            smtp_port = int(os.environ.get("SMTP_PORT", "587"))

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: _send_smtp(mime_msg, smtp_host, smtp_port, str(cfg["user"]), str(cfg["password"])),
            )
        except Exception as exc:
            logger.warning("Failed to send reply email: %s", exc)


def _send_smtp(msg: object, host: str, port: int, user: str, password: str) -> None:
    import smtplib
    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)  # type: ignore[arg-type]
```

#### Celery task (add to `app/scaling/celery_app.py`)

```python
# Add to celery beat schedule:
#   "check-email-goals": {
#       "task": "app.integrations.email.tasks.check_email_goals",
#       "schedule": 60.0,  # every 60 seconds
#   }
```

**New file: `app/integrations/email/tasks.py`**

```python
"""Celery task for periodic email polling."""

from __future__ import annotations

import asyncio
import logging

from app.scaling.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.integrations.email.tasks.check_email_goals", bind=True)
def check_email_goals(self) -> dict:  # type: ignore[type-arg]
    """Periodic Celery task — poll IMAP for new goal-submission emails."""
    from app.integrations.email.imap_listener import IMAPGoalListener
    from app.integrations.email.imap_listener import _is_email_enabled

    if not _is_email_enabled():
        return {"skipped": True, "reason": "IMAP not configured"}

    try:
        # Build minimal context for task execution
        from app.services.goal_service import GoalService
        from app.providers.fake import FakeProvider
        from app.governance.hitl import HITLGateway
        from app.governance.audit import AuditLog
        from app.tenancy.context import PlanTier, TenantContext
        import os

        svc = GoalService(
            provider=FakeProvider(),
            hitl_gateway=HITLGateway(),
            audit_log=AuditLog(),
        )
        tenant = TenantContext(
            tenant_id=os.getenv("IMAP_TENANT_ID", "default"),
            plan_tier=PlanTier.PRO,
            api_key=os.getenv("IMAP_AGENTVERSE_API_KEY", ""),
            settings={},
        )
        listener = IMAPGoalListener(svc, tenant)
        created = asyncio.run(listener.poll_once())
        return {"goals_created": created}
    except Exception as exc:
        logger.exception("check_email_goals task failed: %s", exc)
        return {"error": str(exc)}
```

**pyproject.toml addition:**

```toml
email = [
    "aioimaplib>=2.0.0",
]
```

---

### 3.5 WebSocket Tool Support

#### `app/mcp/ws_client.py`

```python
"""MCPWebSocketClient — WebSocket transport for real-time MCP tool servers.

Used for connectors that push data (stock feeds, IoT, live dashboards).
Supports:
  - Subscribe to streaming tool outputs
  - Handle bidirectional WebSocket tool calls
  - Reconnection with exponential backoff
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RECONNECT_BASE = 1.0   # seconds
_RECONNECT_MAX  = 60.0  # seconds
_RECONNECT_JITTER = 0.1


class MCPWebSocketClient:
    """Async WebSocket client for MCP connectors that support WS transport.

    Usage::

        client = MCPWebSocketClient("ws://iot-connector:8080/ws")
        async with client.connect() as session:
            async for event in session.subscribe("temperature_feed", interval=5):
                print(event)
    """

    def __init__(
        self,
        ws_url: str,
        auth_token: str | None = None,
        reconnect: bool = True,
        max_reconnects: int = 10,
    ) -> None:
        self._ws_url = ws_url
        self._auth_token = auth_token
        self._reconnect = reconnect
        self._max_reconnects = max_reconnects
        self._ws: Any | None = None  # websockets.WebSocketClientProtocol
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._subscriptions: dict[str, asyncio.Queue[Any]] = {}
        self._msg_id = 0
        self._connected = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> "MCPWebSocketClient":
        """Open WebSocket connection. Returns self for use as async context manager."""
        try:
            import websockets  # type: ignore[import]
        except ImportError:
            raise RuntimeError(
                "websockets is not installed. Install with: pip install websockets"
            )

        headers = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        self._ws = await websockets.connect(self._ws_url, extra_headers=headers)
        self._connected = True
        logger.info("MCPWebSocketClient connected to %s", self._ws_url)
        # Start message dispatcher
        asyncio.create_task(self._dispatch_messages())
        return self

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "MCPWebSocketClient":
        return await self.connect()

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Message dispatcher
    # ------------------------------------------------------------------

    async def _dispatch_messages(self) -> None:
        """Background task — reads WebSocket messages and routes to waiters."""
        reconnects = 0
        while self._connected and reconnects <= self._max_reconnects:
            try:
                async for raw_msg in self._ws:  # type: ignore[union-attr]
                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON WS message: %s", raw_msg[:100])
                        continue

                    msg_id: str | None = msg.get("id")
                    msg_type: str = msg.get("type", "")
                    channel: str = msg.get("channel", "")

                    # Route RPC responses to pending futures
                    if msg_id and msg_id in self._pending:
                        future = self._pending.pop(msg_id)
                        if "error" in msg:
                            future.set_exception(RuntimeError(msg["error"]))
                        else:
                            future.set_result(msg.get("result"))

                    # Route subscription events to queues
                    elif channel and channel in self._subscriptions:
                        await self._subscriptions[channel].put(msg.get("data"))

                    elif msg_type == "ping":
                        await self._ws.send(json.dumps({"type": "pong"}))  # type: ignore[union-attr]

                reconnects = 0  # reset on clean exit

            except Exception as exc:
                logger.warning("WS connection lost: %s", exc)
                if not self._reconnect or reconnects >= self._max_reconnects:
                    break
                reconnects += 1
                delay = min(_RECONNECT_BASE * (2 ** reconnects) + _RECONNECT_JITTER, _RECONNECT_MAX)
                logger.info("Reconnecting in %.1fs (attempt %d/%d)", delay, reconnects, self._max_reconnects)
                await asyncio.sleep(delay)
                try:
                    await self.connect()
                except Exception as conn_exc:
                    logger.warning("Reconnect failed: %s", conn_exc)

    # ------------------------------------------------------------------
    # RPC (request/response)
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> Any:
        """Call a tool via WebSocket RPC and await the result.

        Args:
            tool_name: MCP tool name.
            arguments: Tool input arguments.
            timeout: Seconds to wait for a response.

        Returns:
            Tool output (parsed JSON).
        """
        self._msg_id += 1
        msg_id = str(self._msg_id)
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        await self._ws.send(json.dumps({  # type: ignore[union-attr]
            "id": msg_id,
            "type": "call_tool",
            "tool": tool_name,
            "arguments": arguments,
        }))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"WebSocket tool call '{tool_name}' timed out after {timeout}s")

    # ------------------------------------------------------------------
    # Streaming subscriptions
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        channel: str,
        params: dict[str, Any] | None = None,
        buffer_size: int = 100,
    ) -> AsyncIterator[Any]:
        """Subscribe to a real-time event channel.

        Yields data payloads from the channel as they arrive.

        Args:
            channel: Channel / feed name (e.g. "temperature_feed", "stock.AAPL").
            params: Optional subscription parameters (e.g. {"interval": 5}).
            buffer_size: Internal queue buffer size.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=buffer_size)
        self._subscriptions[channel] = queue

        # Send subscribe message
        self._msg_id += 1
        await self._ws.send(json.dumps({  # type: ignore[union-attr]
            "id": str(self._msg_id),
            "type": "subscribe",
            "channel": channel,
            "params": params or {},
        }))

        try:
            while True:
                data = await queue.get()
                if data is None:  # None = unsubscribe signal
                    break
                yield data
        finally:
            self._subscriptions.pop(channel, None)
            # Send unsubscribe
            try:
                self._msg_id += 1
                await self._ws.send(json.dumps({  # type: ignore[union-attr]
                    "id": str(self._msg_id),
                    "type": "unsubscribe",
                    "channel": channel,
                }))
            except Exception:
                pass

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools on the WS MCP server."""
        return await self.call_tool("__list_tools__", {})
```

**pyproject.toml addition:**

```toml
ws-tools = [
    "websockets>=13.0",
]
```

---

## 4. Tests

### 4.1 `tests/integrations/test_zapier.py`

```python
"""Unit tests for Zapier integration router."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from unittest.mock import AsyncMock, patch

from app.integrations.zapier.router import router

app = FastAPI()
app.include_router(router)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_trigger_requires_auth(client):
    resp = await client.post(
        "/integrations/zapier/trigger",
        json={"goal": "Test"},
        headers={"X-Zapier-Secret": "wrong"},
    )
    assert resp.status_code == 401


async def test_poll_returns_empty_list(client):
    import os
    os.environ["ZAPIER_SECRET"] = "test-secret"
    resp = await client.get(
        "/integrations/zapier/poll",
        headers={"X-Zapier-Secret": "test-secret"},
    )
    # Returns empty list when nothing has been added
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

### 4.2 `tests/integrations/test_ws_client.py`

```python
"""Unit tests for MCPWebSocketClient."""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.ws_client import MCPWebSocketClient


async def test_call_tool_routes_response():
    """Verify call_tool sends a WS message and resolves with the response."""
    client = MCPWebSocketClient("ws://localhost:9999/ws")
    mock_ws = AsyncMock()

    # Simulate server responding immediately
    async def fake_send(msg: str) -> None:
        payload = json.loads(msg)
        msg_id = payload["id"]
        future = client._pending.get(msg_id)
        if future:
            future.set_result({"answer": 42})

    mock_ws.send = fake_send
    client._ws = mock_ws
    client._connected = True

    result = await client.call_tool("compute", {"x": 1}, timeout=5.0)
    assert result == {"answer": 42}


async def test_subscribe_yields_events():
    """Verify subscribe() yields data pushed to the internal queue."""
    client = MCPWebSocketClient("ws://localhost:9999/ws")
    mock_ws = AsyncMock()

    async def fake_send(msg: str) -> None:
        payload = json.loads(msg)
        if payload.get("type") == "subscribe":
            channel = payload["channel"]
            queue = client._subscriptions.get(channel)
            if queue:
                await queue.put({"temp": 22.5})
                await queue.put(None)  # signal end

    mock_ws.send = fake_send
    client._ws = mock_ws
    client._connected = True

    events = []
    async for data in client.subscribe("temperature"):
        events.append(data)

    assert len(events) == 1
    assert events[0]["temp"] == 22.5
```

### 4.3 `tests/integrations/test_imap_listener.py`

```python
"""Unit tests for IMAPGoalListener."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.integrations.email.imap_listener import IMAPGoalListener, _is_email_enabled
import os


def test_is_email_enabled_false_when_no_env():
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        os.environ.pop(k, None)
    assert _is_email_enabled() is False


def test_is_email_enabled_true_when_configured():
    os.environ.update({
        "IMAP_HOST": "imap.gmail.com",
        "IMAP_USER": "user@gmail.com",
        "IMAP_PASSWORD": "secret",
    })
    assert _is_email_enabled() is True
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        os.environ.pop(k, None)


async def test_poll_once_skips_when_not_configured():
    mock_svc = AsyncMock()
    mock_tenant = MagicMock()
    listener = IMAPGoalListener(mock_svc, mock_tenant)
    result = await listener.poll_once()
    assert result == 0
    mock_svc.submit_goal.assert_not_called()
```

---

## 5. Docker-Compose Changes

Add to `agent-verse-backend/infra/docker-compose.yml`:

```yaml
  # Email goal listener (opt-in; requires IMAP_* env vars)
  email-worker:
    build:
      context: ..
      dockerfile: Dockerfile
    command: ["celery", "-A", "app.scaling.celery_app", "worker",
              "--loglevel=info", "-Q", "email", "--concurrency=1"]
    environment:
      DATABASE_URL: postgresql+asyncpg://agentverse:agentverse@postgres:5432/agentverse
      REDIS_URL: redis://redis:6379/0
      IMAP_HOST: ${IMAP_HOST:-}
      IMAP_USER: ${IMAP_USER:-}
      IMAP_PASSWORD: ${IMAP_PASSWORD:-}
      IMAP_TENANT_ID: ${IMAP_TENANT_ID:-default}
    profiles: [email]
    depends_on:
      redis:
        condition: service_healthy
```

Environment variables to add to backend service:

```yaml
      SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN:-}
      SLACK_SIGNING_SECRET: ${SLACK_SIGNING_SECRET:-}
      ZAPIER_SECRET: ${ZAPIER_SECRET:-}
```

---

## 6. Acceptance Criteria

```bash
# Integration tests
cd agent-verse-backend && pytest tests/integrations/ -v

# Zapier trigger (manual)
curl -X POST http://localhost:8000/integrations/zapier/trigger \
  -H "X-Zapier-Secret: $ZAPIER_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"goal": "List all open Jira tickets"}'

# Zapier poll
curl http://localhost:8000/integrations/zapier/poll \
  -H "X-Zapier-Secret: $ZAPIER_SECRET"

# GitHub Action (in sample repo)
# .github/workflows/agentverse.yml:
# - uses: agentverse/run-goal-action@v1
#   with:
#     api-key: ${{ secrets.AGENTVERSE_API_KEY }}
#     goal: "Run smoke tests and report results"
```
