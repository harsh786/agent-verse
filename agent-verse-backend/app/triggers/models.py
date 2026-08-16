"""Trigger type definitions — 58 trigger types across 9 families.

Families:
  A. Time/Schedule   — CRON, INTERVAL, ONCE, BUSINESS_CALENDAR, RELATIVE_DELAY, DEADLINE
  B. Goal/Chain      — GOAL_COMPLETED, GOAL_FAILED, GOAL_SCORE_BELOW, HITL_APPROVED,
                       HITL_REJECTED, MEMORY_CREATED
  C. Conversational  — CHAT_COMMAND, CHAT_KEYWORD, CHAT_MENTION, EMAIL_INTENT, SMS_INBOUND,
                       VOICE_TRANSCRIPT, MEETING_ENDED, FORM_SUBMISSION
  D. Condition/State — CONDITION, COUNTER_THRESHOLD, COMPOUND, STATE_TRANSITION, WINDOW_AGGREGATE
  E. External Events — EVENT, WEBHOOK, REST, GITHUB_WEBHOOK, JIRA_WEBHOOK, STRIPE_WEBHOOK,
                       SLACK_EVENT, TEAMS_WEBHOOK, DISCORD_EVENT, SALESFORCE_EVENT,
                       CONFLUENCE_WEBHOOK, LINEAR_WEBHOOK
  F. Data/File       — FILE_DROP, DB_ROW_CHANGE, S3_EVENT, EMAIL_ARRIVAL, RSS_FEED,
                       GOOGLE_SHEETS, SHAREPOINT
  G. Monitoring      — ALERTMANAGER, DATADOG, PAGERDUTY, GRAFANA_ALERT, CLOUDWATCH,
                       SENTRY_ISSUE, LOG_PATTERN
  H. API/Polling     — API_POLL, GRAPHQL_SUBSCRIPTION, WEBSOCKET_MESSAGE, PRICE_THRESHOLD
  I. IoT/Edge        — MQTT, GEOFENCE, SENSOR_THRESHOLD
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class TriggerType(enum.StrEnum):
    # ── Family A: Time/Schedule ──────────────────────────────────────────────
    CRON               = "cron"               # existing
    INTERVAL           = "interval"           # existing
    ONCE               = "once"               # existing
    BUSINESS_CALENDAR  = "business_calendar"  # NEW
    RELATIVE_DELAY     = "relative_delay"     # NEW
    DEADLINE           = "deadline"           # NEW

    # ── Family B: Goal/Agent Chain ───────────────────────────────────────────
    GOAL_COMPLETED     = "goal_completed"     # NEW
    GOAL_FAILED        = "goal_failed"        # NEW
    GOAL_SCORE_BELOW   = "goal_score_below"   # NEW
    HITL_APPROVED      = "hitl_approved"      # NEW
    HITL_REJECTED      = "hitl_rejected"      # NEW
    MEMORY_CREATED     = "memory_created"     # NEW

    # ── Family C: Conversational/Chat ────────────────────────────────────────
    CHAT_COMMAND       = "chat_command"       # NEW
    CHAT_KEYWORD       = "chat_keyword"       # NEW
    CHAT_MENTION       = "chat_mention"       # NEW
    EMAIL_INTENT       = "email_intent"       # NEW
    SMS_INBOUND        = "sms_inbound"        # NEW
    VOICE_TRANSCRIPT   = "voice_transcript"   # NEW
    MEETING_ENDED      = "meeting_ended"      # NEW
    FORM_SUBMISSION    = "form_submission"    # NEW

    # ── Family D: Condition/State ────────────────────────────────────────────
    CONDITION          = "condition"          # NEW
    COUNTER_THRESHOLD  = "counter_threshold"  # NEW
    COMPOUND           = "compound"           # NEW
    STATE_TRANSITION   = "state_transition"   # NEW
    WINDOW_AGGREGATE   = "window_aggregate"   # NEW

    # ── Family E: External Events / Webhooks ─────────────────────────────────
    EVENT              = "event"              # existing (Redis pub/sub channel)
    WEBHOOK            = "webhook"            # existing
    REST               = "rest"               # existing
    GITHUB_WEBHOOK     = "github_webhook"     # NEW
    JIRA_WEBHOOK       = "jira_webhook"       # NEW
    STRIPE_WEBHOOK     = "stripe_webhook"     # NEW
    SLACK_EVENT        = "slack_event"        # NEW
    TEAMS_WEBHOOK      = "teams_webhook"      # NEW
    DISCORD_EVENT      = "discord_event"      # NEW
    SALESFORCE_EVENT   = "salesforce_event"   # NEW
    CONFLUENCE_WEBHOOK = "confluence_webhook" # NEW
    LINEAR_WEBHOOK     = "linear_webhook"     # NEW

    # ── Family F: Data/File/Storage ──────────────────────────────────────────
    FILE_DROP          = "file_drop"          # existing
    DB_ROW_CHANGE      = "db_row_change"      # NEW
    S3_EVENT           = "s3_event"           # NEW
    EMAIL_ARRIVAL      = "email_arrival"      # NEW
    RSS_FEED           = "rss_feed"           # NEW
    GOOGLE_SHEETS      = "google_sheets"      # NEW
    SHAREPOINT         = "sharepoint"         # NEW

    # ── Family G: Monitoring/Alerting ────────────────────────────────────────
    ALERTMANAGER       = "alertmanager"       # existing
    DATADOG            = "datadog"            # existing
    PAGERDUTY          = "pagerduty"          # existing
    GRAFANA_ALERT      = "grafana_alert"      # NEW
    CLOUDWATCH         = "cloudwatch"         # NEW
    SENTRY_ISSUE       = "sentry_issue"       # NEW
    LOG_PATTERN        = "log_pattern"        # NEW

    # ── Family H: API/Polling ────────────────────────────────────────────────
    API_POLL               = "api_poll"               # NEW
    GRAPHQL_SUBSCRIPTION   = "graphql_subscription"   # NEW
    WEBSOCKET_MESSAGE      = "websocket_message"      # NEW
    PRICE_THRESHOLD        = "price_threshold"        # NEW

    # ── Family I: IoT/Edge ───────────────────────────────────────────────────
    MQTT               = "mqtt"               # NEW
    GEOFENCE           = "geofence"           # NEW
    SENSOR_THRESHOLD   = "sensor_threshold"   # NEW


# ── TriggerSpec ───────────────────────────────────────────────────────────────

@dataclass
class TriggerSpec:
    """Complete configuration for any of the 58 trigger types."""

    # ── Core ──────────────────────────────────────────────────────────────────
    trigger_type:          TriggerType = TriggerType.ONCE
    description:           str = ""
    goal_template:         str = ""          # NL template; {{payload.field}} interpolation
    condition:             str = ""          # CEL expression gating all trigger types
    priority:              str = "normal"    # high | normal | low
    max_firings_per_hour:  int = 0           # 0 = unlimited; rate cap per trigger
    expires_at_iso:        str = ""          # ISO datetime after which trigger auto-disables
    on_failure_notify:     str = ""          # email/Slack channel on dispatch failure
    tags:                  list = field(default_factory=list)

    # ── Family A: Time/Schedule ───────────────────────────────────────────────
    cron_expression:      str = ""
    timezone:             str = "UTC"
    interval_seconds:     int = 0
    fire_at_iso:          str = ""
    business_calendar_id: str = ""           # reference to a BusinessCalendar object
    relative_to_field:    str = ""           # JSONPath into triggering context
    relative_offset_seconds: int = 0         # positive = after, negative = before
    deadline_field:       str = ""           # field containing the deadline ISO timestamp
    deadline_warning_seconds: int = 0        # fire N seconds before deadline

    # ── Family B: Goal/Agent Chain ────────────────────────────────────────────
    watch_goal_id:        str = ""           # specific goal ID to watch, or "" = any
    watch_agent_id:       str = ""           # filter by agent
    score_threshold:      float = 0.0
    score_dimension:      str = ""           # "overall" | specific dimension name
    hitl_queue_id:        str = ""           # for HITL_APPROVED / HITL_REJECTED
    memory_type:          str = ""           # for MEMORY_CREATED: type of memory

    # ── Family C: Conversational ──────────────────────────────────────────────
    channel_type:         str = ""           # "slack" | "teams" | "discord" | "sms" | ...
    channel_id:           str = ""           # specific channel, DM, or phone number
    command_pattern:      str = ""           # exact command string e.g. "/run-agent"
    keyword_pattern:      str = ""           # regex or keyword list (comma-separated)
    mention_bot_id:       str = ""           # bot user ID to watch for @mentions
    email_sender_filter:  str = ""           # regex on sender address
    email_subject_pattern: str = ""          # regex on subject line
    phone_number_filter:  str = ""           # regex on inbound phone number (SMS/voice)
    voice_language:       str = "en-US"      # language for transcript parsing
    meeting_platform:     str = ""           # "zoom" | "teams" | "google_meet"
    form_id:              str = ""           # form identifier

    # ── Family D: Condition/State ─────────────────────────────────────────────
    condition_expression: str = ""           # CEL expression for CONDITION type
    counter_key:          str = ""           # Redis key for COUNTER_THRESHOLD
    counter_threshold:    int = 0            # fire when counter reaches this value
    counter_window_secs:  int = 3600         # sliding window for counter
    compound_logic:       str = "AND"        # "AND" | "OR" for COMPOUND
    compound_trigger_ids: list = field(default_factory=list)
    state_machine_id:     str = ""           # for STATE_TRANSITION
    from_state:           str = ""
    to_state:             str = ""
    window_seconds:       int = 300          # for WINDOW_AGGREGATE
    window_field:         str = ""           # JSONPath to aggregate field
    window_aggregation:   str = "sum"        # sum | avg | max | min | count
    window_threshold:     float = 0.0

    # ── Family E: External Events / Webhooks ──────────────────────────────────
    event_channel:        str = ""           # Redis pub/sub channel for EVENT type
    event_filter:         str = ""           # JSONPath filter for EVENT type
    webhook_token:        str = ""           # for WEBHOOK / REST types
    webhook_signature_secret: str = ""       # vault://key-name or raw secret
    allowed_api_keys:     list = field(default_factory=list)
    github_event_filter:  str = ""           # e.g. "push", "pull_request.merged"
    jira_project_filter:  str = ""
    stripe_event_filter:  str = ""
    discord_server_id:    str = ""
    discord_channel_id:   str = ""
    salesforce_object:    str = ""
    teams_team_id:        str = ""
    teams_channel_id:     str = ""
    confluence_space_key: str = ""
    linear_team_id:       str = ""

    # ── Family F: Data/File/Storage ───────────────────────────────────────────
    file_drop_path:       str = ""           # watched directory path
    db_table:             str = ""           # table to watch for DB_ROW_CHANGE
    db_operation:         str = ""           # INSERT | UPDATE | DELETE | *
    db_filter:            str = ""           # SQL WHERE clause for filter
    s3_bucket:            str = ""
    s3_prefix:            str = ""
    s3_events:            list = field(default_factory=list)  # ["s3:ObjectCreated:*"]
    rss_url:              str = ""
    sheets_spreadsheet_id: str = ""
    sheets_range:         str = ""
    sharepoint_site_url:  str = ""
    sharepoint_library:   str = ""

    # ── Family G: Monitoring/Alerting ─────────────────────────────────────────
    alert_severity_filter: str = ""          # "critical" | "warning" | "*"
    alert_labels:         dict = field(default_factory=dict)
    log_pattern_regex:    str = ""
    log_stream:           str = ""
    cloudwatch_namespace: str = ""
    cloudwatch_metric:    str = ""
    sentry_project:       str = ""
    sentry_environment:   str = ""

    # ── Family H: API/Polling ─────────────────────────────────────────────────
    poll_url:             str = ""
    poll_method:          str = "GET"
    poll_headers:         dict = field(default_factory=dict)
    poll_body:            dict = field(default_factory=dict)
    poll_jsonpath:        str = ""           # JSONPath to extract value for comparison
    poll_expected_value:  str = ""
    poll_interval_seconds: int = 300
    graphql_endpoint:     str = ""
    graphql_subscription_query: str = ""
    websocket_url:        str = ""
    websocket_message_pattern: str = ""
    price_symbol:         str = ""           # e.g. "BTC/USD"
    price_threshold:      float = 0.0
    price_direction:      str = "above"      # "above" | "below"

    # ── Family I: IoT/Edge ────────────────────────────────────────────────────
    mqtt_broker_url:      str = ""
    mqtt_topic:           str = ""
    mqtt_qos:             int = 0
    geofence_polygon:     list = field(default_factory=list)  # [[lat, lon], ...]
    geofence_action:      str = "enter"      # "enter" | "exit" | "both"
    sensor_device_id:     str = ""
    sensor_metric:        str = ""
    sensor_threshold:     float = 0.0
    sensor_comparison:    str = "gt"         # gt | gte | lt | lte | eq

    # ── Security / RBAC ───────────────────────────────────────────────────────
    allowed_roles:        list = field(default_factory=lambda: ["admin", "developer", "operator"])
    simulation_mode:      bool = False       # if True: dispatch but don't create goals

    # ── Versioning ────────────────────────────────────────────────────────────
    version:              int = 1


def validate_cron(expression: str, plan: str = "free") -> None:
    """Validate a cron expression and check plan-tier minimum interval.

    Raises ValueError if the expression is invalid or violates plan limits.
    """
    try:
        from croniter import croniter
        croniter(expression)
    except ImportError:
        pass  # croniter not installed — skip validation
    except Exception as exc:
        raise ValueError(f"Invalid cron expression '{expression}': {exc}") from exc

