"""NL Scheduler — parses NL schedule/trigger descriptions into TriggerSpecs.

Covers all 58 TriggerType values across 9 families (A-I).
Fast path: keyword rules → instant TriggerSpec (no LLM).
Slow path: LLM provider for ambiguous or novel descriptions.

Examples:
  "Every weekday at 9 AM UTC"           → CRON  (Family A)
  "When the invoice agent finishes"     → GOAL_COMPLETED (Family B)
  "When someone types /run in Slack"    → CHAT_COMMAND (Family C)
  "When CPU usage exceeds 90%"          → WINDOW_AGGREGATE (Family D→G)
  "When GitHub PR is merged"            → GITHUB_WEBHOOK (Family E)
  "When a new row is inserted in users" → DB_ROW_CHANGE (Family F)
  "When PagerDuty fires a P1 alert"     → PAGERDUTY (Family G)
  "Poll the API every 5 minutes"        → API_POLL (Family H)
  "When temperature sensor > 80°C"     → SENSOR_THRESHOLD (Family I)
  "When device enters the office zone"  → GEOFENCE (Family I)
  "When Bitcoin drops below $60k"       → PRICE_THRESHOLD (Family H)
  "When a file drops into the S3 bucket"→ S3_EVENT (Family F)
"""

from __future__ import annotations

import json
import re

from app.providers.base import CompletionRequest, LLMProvider, Message
from app.triggers.models import TriggerSpec, TriggerType

# ── Keyword routing: (pattern, TriggerType, {extra_spec_fields}) ──────────────
# Ordered from most-specific to least-specific.
_KEYWORD_RULES: list[tuple[re.Pattern, TriggerType, dict]] = [
    # A. Time / Schedule
    # More-specific rules MUST come before general "every X" patterns
    # API_POLL: "poll the API every 5 min" / "check URL every hour"
    (re.compile(
        r"\bpoll\b.*\bapi\b|\bapi\b.*\bpoll\b|\bcheck\b.*\burl\b.*\bevery\b"
        r"|\bpoll\s+the\s+api\b|\bhttp.*poll\b",
        re.I,
    ), TriggerType.API_POLL, {}),
    (re.compile(
        r"\bevery\b.*(minute|hour|day|week|month|weekday)\b|\bcron\b", re.I,
    ), TriggerType.CRON, {}),
    (re.compile(
        r"\bevery\b\s+\d+\s*(seconds?|minutes?|hours?)\b", re.I,
    ), TriggerType.INTERVAL, {}),
    (re.compile(r"\bonce\b|\bone[\s-]?time\b|\bat\s+\d", re.I), TriggerType.ONCE, {}),
    # RELATIVE_DELAY before DEADLINE
    (re.compile(
        r"\brelative\b.*\bdelay\b|\bN?\s*\d+\s+days?\s+before\b"
        r"|\bafter\s+.*\s+days?\b|\b\d+\s+days?\s+before\b",
        re.I,
    ), TriggerType.RELATIVE_DELAY, {}),
    (re.compile(r"\bdeadline\b|\bdue\s+date\b|\bexpir", re.I), TriggerType.DEADLINE, {}),
    (re.compile(
        r"\bbusiness\s+hours?\b|\bworking\s+hours?\b", re.I,
    ), TriggerType.BUSINESS_CALENDAR, {}),
    # B. Goal Chain
    (re.compile(
        r"\b(goal|agent|task)\b.*\bfin(ishes?|ished|ishes?)\b|\bwhen.*complet", re.I,
    ), TriggerType.GOAL_COMPLETED, {}),
    (re.compile(
        r"\b(goal|agent|task)\b.*\bfails?\b|\bfailure\b", re.I,
    ), TriggerType.GOAL_FAILED, {}),
    (re.compile(
        r"\bscore\b.*\bbelow\b|\blow\b.*\bscore\b", re.I,
    ), TriggerType.GOAL_SCORE_BELOW, {}),
    (re.compile(r"\bhitl\b|\bhuman\s+approv", re.I), TriggerType.HITL_APPROVED, {}),
    (re.compile(r"\bhuman\s+reject", re.I), TriggerType.HITL_REJECTED, {}),
    (re.compile(
        r"\bmemory\b.*\bcreated?\b|\bnew\s+memory\b", re.I,
    ), TriggerType.MEMORY_CREATED, {}),
    # C. Conversational
    (re.compile(
        r"\bslack\s+command\b|\btype[s]?\s+/\w+\b|\bchat\s+command\b"
        r"|\b/run\b|\b/deploy\b",
        re.I,
    ), TriggerType.CHAT_COMMAND, {}),
    (re.compile(
        r"\bkeyword\b|\bchat\s+keyword\b|\bwhen.*mention\b.*\bword\b", re.I,
    ), TriggerType.CHAT_KEYWORD, {}),
    (re.compile(
        r"\bmentioned?\b|\b@bot\b|\bchat\s+mention\b", re.I,
    ), TriggerType.CHAT_MENTION, {}),
    (re.compile(r"\bslack\s+event\b", re.I), TriggerType.SLACK_EVENT, {}),
    (re.compile(
        r"\bteams\b.*\bwebhook\b|\bmicrosoft\s+teams\b", re.I,
    ), TriggerType.TEAMS_WEBHOOK, {}),
    (re.compile(r"\bdiscord\b", re.I), TriggerType.DISCORD_EVENT, {}),
    (re.compile(
        r"\bemail\b.*\bintent\b|\binbound.*email\b|\bemail.*classif", re.I,
    ), TriggerType.EMAIL_INTENT, {}),
    (re.compile(r"\bemail\b.*\barriv|\bnew\s+email\b", re.I), TriggerType.EMAIL_ARRIVAL, {}),
    (re.compile(r"\bsms\b|\btext\s+message\b|\btwilio\b", re.I), TriggerType.SMS_INBOUND, {}),
    (re.compile(
        r"\bvoice\b.*\btranscript\b|\bspeech\b", re.I,
    ), TriggerType.VOICE_TRANSCRIPT, {}),
    (re.compile(
        r"\bmeeting\s+end\b|\bmeeting\s+finish", re.I,
    ), TriggerType.MEETING_ENDED, {}),
    (re.compile(r"\bform\s+submiss|\bform\s+fill", re.I), TriggerType.FORM_SUBMISSION, {}),
    # D. Condition/State
    (re.compile(
        r"\bstate\s+(machine\s+)?transition\b|\bstate\s+change\b", re.I,
    ), TriggerType.STATE_TRANSITION, {}),
    (re.compile(
        r"\bcondition\b.*\btrue\b|\bcel\b.*\bexpression\b|\bwhen\s+.*\s*==\s*", re.I,
    ), TriggerType.CONDITION, {}),
    (re.compile(
        r"\bcount(?:er|ed)?\b.*\bthreshold\b|\b\d+\s+times?\b.*\bwindow\b", re.I,
    ), TriggerType.COUNTER_THRESHOLD, {}),
    (re.compile(
        r"\bwindow\b.*\baggreg|\baverage\b.*\bexceed|\bsum\b.*\bthreshold", re.I,
    ), TriggerType.WINDOW_AGGREGATE, {}),
    (re.compile(r"\bcompound\b|\band\b.*\bor\b.*\btrigger\b", re.I), TriggerType.COMPOUND, {}),
    (re.compile(
        r"\bfeature\s+flag\b|\bflag\s+change\b", re.I,
    ), TriggerType.EVENT, {}),  # event type for flags
    # E. Webhooks
    (re.compile(
        r"\bgithub\b|\bpr\s+merged?\b|\bgit\s+push\b|\bpull\s+request\b", re.I,
    ), TriggerType.GITHUB_WEBHOOK, {}),
    (re.compile(r"\bjira\b", re.I), TriggerType.JIRA_WEBHOOK, {}),
    (re.compile(
        r"\bstripe\b|\bpayment\s+(received?|succeeded?)\b", re.I,
    ), TriggerType.STRIPE_WEBHOOK, {}),
    (re.compile(r"\bpagerduty\b|\bpd\s+alert\b", re.I), TriggerType.PAGERDUTY, {}),
    (re.compile(r"\blinear\b.*\b(issue|ticket)\b", re.I), TriggerType.LINEAR_WEBHOOK, {}),
    (re.compile(r"\bsalesforce\b", re.I), TriggerType.SALESFORCE_EVENT, {}),
    (re.compile(r"\bconfluence\b", re.I), TriggerType.CONFLUENCE_WEBHOOK, {}),
    (re.compile(r"\bwebhook\b", re.I), TriggerType.WEBHOOK, {}),
    # F. Data
    (re.compile(
        r"\bdatabase\b.*\brow\b|\bdb\b.*\binsert\b|\bdb\b.*\bupdate\b|\bnew\s+row\b", re.I,
    ), TriggerType.DB_ROW_CHANGE, {}),
    (re.compile(
        r"\bs3\b|\bfile\s+drop[ps]?\b.*\bbucket\b|\bnew\s+file\b.*\bs3\b", re.I,
    ), TriggerType.S3_EVENT, {}),
    (re.compile(r"\bfile\s+drop[ps]?\b", re.I), TriggerType.FILE_DROP, {}),
    (re.compile(r"\brss\b|\batom\s+feed\b|\bnews\s+feed\b", re.I), TriggerType.RSS_FEED, {}),
    (re.compile(r"\bgoogle\s+sheets?\b", re.I), TriggerType.GOOGLE_SHEETS, {}),
    (re.compile(r"\bsharepoint\b", re.I), TriggerType.SHAREPOINT, {}),
    # G. Monitoring
    (re.compile(r"\bgrafana\b", re.I), TriggerType.GRAFANA_ALERT, {}),
    (re.compile(r"\bcloudwatch\b", re.I), TriggerType.CLOUDWATCH, {}),
    (re.compile(r"\bsentry\b", re.I), TriggerType.SENTRY_ISSUE, {}),
    (re.compile(r"\bpagerduty\b", re.I), TriggerType.PAGERDUTY, {}),
    (re.compile(r"\bdatadog\b", re.I), TriggerType.DATADOG, {}),
    (re.compile(
        r"\balerting?\b.*\bmanager\b|\balertmanager\b", re.I,
    ), TriggerType.ALERTMANAGER, {}),
    (re.compile(
        r"\blog\s+pattern\b|\bregex\b.*\blog\b|\bmatches?\b.*\blog\b", re.I,
    ), TriggerType.LOG_PATTERN, {}),
    (re.compile(
        r"\bmetric\b.*\bthreshold\b|\b(cpu|memory|disk)\b.*\b(exceed|above|over)\b", re.I,
    ), TriggerType.WINDOW_AGGREGATE, {}),
    # H. Advanced / Polling
    (re.compile(r"\bgraphql\b.*\bsubscri", re.I), TriggerType.GRAPHQL_SUBSCRIPTION, {}),
    (re.compile(
        r"\bwebsocket\b.*\bmessage\b|\bws\b.*\bmessage\b", re.I,
    ), TriggerType.WEBSOCKET_MESSAGE, {}),
    (re.compile(
        r"\bprice\b.*\b(drop[ps]?|fall[s]?|below|above|rise[s]?|threshold)\b"
        r"|\bbitcoin\b|\bcrypto\b|\bstock\b.*\bprice\b",
        re.I,
    ), TriggerType.PRICE_THRESHOLD, {}),
    (re.compile(r"\bkafka\b", re.I), TriggerType.EVENT, {}),  # kafka maps to event
    # I. IoT
    (re.compile(r"\bmqtt\b|\bbroker\b.*\btopic\b", re.I), TriggerType.MQTT, {}),
    (re.compile(
        r"\bgeofence\b|\benter[s]?\b.*\bzone\b|\bleave[s]?\b.*\bzone\b"
        r"|\benter[s]?\b.*\bregion\b",
        re.I,
    ), TriggerType.GEOFENCE, {}),
    (re.compile(
        r"\bsensor\b.*\b(threshold|exceed|above|below)\b"
        r"|\btemperature\b.*\b(exceed|above|below|sensor)\b|\bhumid|\b\d+°[CF]\b",
        re.I,
    ), TriggerType.SENSOR_THRESHOLD, {}),
    # Email arrival fallback (after more-specific email_intent rule)
    (re.compile(r"\bemail\b", re.I), TriggerType.EMAIL_ARRIVAL, {}),
]

_NL_SCHEDULER_SYSTEM = (
    "You are a trigger configuration parser for an AI automation platform.\n"
    "Convert a natural language trigger description into a structured JSON spec.\n"
    "\n"
    "Supported trigger_type values (choose the most specific):\n"
    "Family A (Time): cron, interval, once, business_calendar, relative_delay, deadline\n"
    "Family B (Goal Chain): goal_completed, goal_failed, goal_score_below,\n"
    "  hitl_approved, hitl_rejected, memory_created\n"
    "Family C (Conversational): chat_command, chat_keyword, chat_mention, slack_event,\n"
    "  teams_webhook, discord_event, email_intent, email_arrival, sms_inbound,\n"
    "  voice_transcript, meeting_ended, form_submission\n"
    "Family D (Condition): condition, counter_threshold, compound,\n"
    "  state_transition, window_aggregate\n"
    "Family E (Webhooks): github_webhook, jira_webhook, stripe_webhook,\n"
    "  salesforce_event, confluence_webhook, linear_webhook, webhook\n"
    "Family F (Data): db_row_change, s3_event, file_drop, email_arrival,\n"
    "  rss_feed, google_sheets, sharepoint, api_poll\n"
    "Family G (Monitoring): alertmanager, datadog, pagerduty, grafana_alert,\n"
    "  cloudwatch, sentry_issue, log_pattern\n"
    "Family H (Polling/Advanced): graphql_subscription, websocket_message, price_threshold\n"
    "Family I (IoT): mqtt, geofence, sensor_threshold\n"
    "\n"
    "Respond with ONLY valid JSON. No markdown. No explanation.\n"
    "Include relevant configuration fields alongside trigger_type.\n"
    "Example responses:\n"
    '{"trigger_type": "cron", "cron_expression": "0 9 * * 1-5", "timezone": "UTC"}\n'
    '{"trigger_type": "goal_completed", "watch_agent_id": "invoice-agent"}\n'
    '{"trigger_type": "sensor_threshold", "sensor_metric": "temperature",'
    ' "sensor_threshold": 80, "sensor_comparison": ">"}\n'
    '{"trigger_type": "price_threshold", "price_symbol": "BTC",'
    ' "price_threshold": 60000, "price_direction": "below"}\n'
    '{"trigger_type": "mqtt", "mqtt_topic": "sensors/+/temp",'
    ' "mqtt_broker_url": "mqtt://broker.example.com"}'
)


def _keyword_route(description: str) -> TriggerSpec | None:
    """Fast path: return TriggerSpec if a keyword rule matches."""
    for pattern, trigger_type, extra in _KEYWORD_RULES:
        if pattern.search(description):
            kwargs: dict = {"trigger_type": trigger_type}
            # Extract cron-like patterns
            if trigger_type == TriggerType.INTERVAL:
                m = re.search(r"every\s+(\d+)\s*(second|minute|hour)", description, re.I)
                if m:
                    n = int(m.group(1))
                    unit = m.group(2).lower()
                    multiplier = {"second": 1, "minute": 60, "hour": 3600}.get(unit, 1)
                    kwargs["interval_seconds"] = n * multiplier
            kwargs.update(extra)
            return TriggerSpec(**kwargs)
    return None


def _parse_single(obj: dict) -> TriggerSpec:
    raw_type = str(obj.get("trigger_type", "once"))
    # Raises ValueError for unknown types (callers must handle)
    ttype = TriggerType(raw_type)

    kwargs: dict = {"trigger_type": ttype}
    # Map all recognized fields from LLM response
    field_map = [
        "cron_expression", "timezone", "interval_seconds", "fire_at_iso",
        "condition", "description", "watch_goal_id", "watch_agent_id",
        "score_threshold", "condition_expression", "webhook_signature_secret",
        "mqtt_topic", "mqtt_broker_url", "mqtt_qos",
        "geofence_action", "geofence_polygon",
        "sensor_metric", "sensor_threshold", "sensor_comparison", "sensor_device_id",
        "price_symbol", "price_threshold", "price_direction",
        "poll_url", "poll_method", "poll_interval_seconds", "poll_jsonpath",
        "rss_url", "s3_bucket", "s3_prefix", "db_table", "db_operation",
        "log_pattern_regex", "log_stream",
        "graphql_endpoint", "graphql_subscription_query",
        "state_machine_id", "from_state", "to_state",
        "counter_key", "counter_threshold", "counter_window_secs",
        "channel_type", "channel_id", "command_pattern", "keyword_pattern",
        "email_sender_filter", "email_subject_pattern",
        "hitl_queue_id", "memory_type",
    ]
    for field in field_map:
        if obj.get(field) is not None:
            kwargs[field] = obj[field]
    return TriggerSpec(**kwargs)


class NLScheduler:
    """Converts NL trigger descriptions to TriggerSpecs.

    Primary path: LLM provider for accurate structured extraction.
    Fast path (fallback): regex keyword rules when LLM is unavailable.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def parse(self, description: str) -> list[TriggerSpec]:
        # Primary path — LLM (preserves cron_expression, timezone, etc.)
        try:
            req = CompletionRequest(
                messages=[
                    Message(role="system", content=_NL_SCHEDULER_SYSTEM),
                    Message(role="user", content=description),
                ],
                model="claude-opus-4-8",
            )
            resp = await self._provider.complete(req)
            text = re.sub(r"```(?:json)?\n?", "", resp.content).strip()

            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                # LLM returned non-JSON — fall through to keyword routing
                raise ValueError("non-json response") from None

            if "schedules" in obj and isinstance(obj["schedules"], list):
                return [_parse_single(s) for s in obj["schedules"]]
            return [_parse_single(obj)]

        except Exception:
            pass

        # Fallback — keyword routing (no LLM calls needed)
        fast = _keyword_route(description)
        if fast is not None:
            return [fast]

        # Last resort
        return [TriggerSpec(trigger_type=TriggerType.ONCE, description=description)]

