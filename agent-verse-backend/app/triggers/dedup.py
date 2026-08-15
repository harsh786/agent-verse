"""Idempotency key derivation for trigger firings.

Each trigger family derives the key differently to ensure
deterministic deduplication without false positives.
"""
from __future__ import annotations

import hashlib
import json


def derive_idempotency_key(
    trigger_id: str,
    trigger_type: str,
    payload: dict,
    *,
    scheduled_fire_time: str | None = None,
    source_goal_id: str | None = None,
    completion_event_id: str | None = None,
    message_id: str | None = None,
    txn_id: str | None = None,
) -> str:
    """Return a deterministic idempotency key for a trigger firing.

    The key is a SHA-256 hex digest (first 32 chars) of a stable string
    composed from the trigger_id and firing-context identifiers.
    """
    family = trigger_type.split("_")[0].lower()

    # Family A: Time-based — keyed on trigger + scheduled fire time
    if trigger_type in ("cron", "interval", "once", "business_calendar",
                        "relative_delay", "deadline"):
        stable = f"{trigger_id}:{scheduled_fire_time or 'now'}"

    # Family B: Goal chain — keyed on trigger + source goal + completion event
    elif trigger_type in ("goal_completed", "goal_failed", "goal_score_below",
                          "hitl_approved", "hitl_rejected", "memory_created"):
        stable = f"{trigger_id}:{source_goal_id or ''}:{completion_event_id or ''}"

    # Family C: Conversational — keyed on trigger + message_id
    elif trigger_type in ("chat_command", "chat_keyword", "chat_mention",
                          "email_intent", "sms_inbound", "voice_transcript",
                          "meeting_ended", "form_submission"):
        stable = f"{trigger_id}:{message_id or _payload_hash(payload)}"

    # Family D: Condition/State — keyed on trigger + payload hash
    elif trigger_type in ("condition", "counter_threshold", "compound",
                          "state_transition", "window_aggregate"):
        stable = f"{trigger_id}:{_payload_hash(payload)}"

    # Family E/G: Webhooks/HTTP — keyed on trigger + request body hash
    elif trigger_type in ("event", "webhook", "rest", "github_webhook",
                          "jira_webhook", "stripe_webhook", "slack_event",
                          "teams_webhook", "discord_event", "salesforce_event",
                          "confluence_webhook", "linear_webhook",
                          "alertmanager", "datadog", "pagerduty",
                          "grafana_alert", "cloudwatch", "sentry_issue",
                          "log_pattern"):
        stable = f"{trigger_id}:{_payload_hash(payload)}"

    # Family F: Data — keyed on trigger + txn_id or payload hash
    elif trigger_type in ("file_drop", "db_row_change", "s3_event",
                          "email_arrival", "rss_feed", "google_sheets",
                          "sharepoint"):
        stable = f"{trigger_id}:{txn_id or _payload_hash(payload)}"

    # Family H/I: Polling/IoT — keyed on trigger + payload hash
    else:
        stable = f"{trigger_id}:{_payload_hash(payload)}"

    digest = hashlib.sha256(stable.encode()).hexdigest()
    return digest[:32]


def _payload_hash(payload: dict) -> str:
    """Return a short stable hash of the payload dict."""
    serialised = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:16]
