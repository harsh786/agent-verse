"""Typed webhook payload parsers for Phase 4."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GitHubWebhookPayload:
    event_type: str = ""
    repo_full_name: str = ""
    sender_login: str = ""
    action: str = ""
    ref: str = ""
    head_commit_message: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, headers: dict, body: dict) -> "GitHubWebhookPayload":
        return cls(
            event_type=headers.get("X-GitHub-Event", headers.get("x-github-event", "")),
            repo_full_name=(body.get("repository") or {}).get("full_name", ""),
            sender_login=(body.get("sender") or {}).get("login", ""),
            action=body.get("action", ""),
            ref=body.get("ref", ""),
            head_commit_message=(body.get("head_commit") or {}).get("message", ""),
            raw=body,
        )


@dataclass
class StripeWebhookPayload:
    event_type: str = ""
    event_id: str = ""
    livemode: bool = False
    object_type: str = ""
    amount: int = 0
    currency: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, body: dict) -> "StripeWebhookPayload":
        obj = body.get("data", {}).get("object", {})
        return cls(
            event_type=body.get("type", ""),
            event_id=body.get("id", ""),
            livemode=body.get("livemode", False),
            object_type=obj.get("object", ""),
            amount=obj.get("amount", 0),
            currency=obj.get("currency", ""),
            raw=body,
        )


@dataclass
class JiraWebhookPayload:
    event_type: str = ""
    issue_key: str = ""
    issue_status: str = ""
    project_key: str = ""
    user_display_name: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, body: dict) -> "JiraWebhookPayload":
        issue = body.get("issue", {})
        fields = issue.get("fields", {})
        status = (fields.get("status") or {}).get("name", "")
        project = (fields.get("project") or {}).get("key", "")
        return cls(
            event_type=body.get("webhookEvent", ""),
            issue_key=issue.get("key", ""),
            issue_status=status,
            project_key=project,
            user_display_name=(body.get("user") or {}).get("displayName", ""),
            raw=body,
        )


@dataclass
class SlackEventPayload:
    event_type: str = ""
    team_id: str = ""
    channel: str = ""
    user: str = ""
    text: str = ""
    ts: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, body: dict) -> "SlackEventPayload":
        event = body.get("event", {})
        return cls(
            event_type=body.get("type", ""),
            team_id=body.get("team_id", ""),
            channel=event.get("channel", ""),
            user=event.get("user", ""),
            text=event.get("text", ""),
            ts=event.get("ts", ""),
            raw=body,
        )


@dataclass
class PagerDutyWebhookPayload:
    event_type: str = ""
    service_name: str = ""
    incident_id: str = ""
    severity: str = ""
    urgency: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, body: dict) -> "PagerDutyWebhookPayload":
        messages = body.get("messages", [body])
        msg = messages[0] if messages else {}
        incident = msg.get("incident", {})
        return cls(
            event_type=msg.get("event", ""),
            service_name=(incident.get("service") or {}).get("name", ""),
            incident_id=incident.get("id", ""),
            severity=incident.get("severity", ""),
            urgency=incident.get("urgency", ""),
            raw=body,
        )


@dataclass
class LinearWebhookPayload:
    event_type: str = ""
    action: str = ""
    issue_id: str = ""
    issue_title: str = ""
    state_name: str = ""
    team_name: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, body: dict) -> "LinearWebhookPayload":
        data = body.get("data", {})
        return cls(
            event_type=body.get("type", ""),
            action=body.get("action", ""),
            issue_id=data.get("id", ""),
            issue_title=data.get("title", ""),
            state_name=(data.get("state") or {}).get("name", ""),
            team_name=(data.get("team") or {}).get("name", ""),
            raw=body,
        )
