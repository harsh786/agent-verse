/**
 * Trigger system TypeScript types.
 * Covers all 58 TriggerType enum values across 9 families.
 */

// ── Trigger Families ──────────────────────────────────────────────────────────

export type TriggerFamily =
  | 'time'
  | 'goal_chain'
  | 'conversational'
  | 'webhook'
  | 'data'
  | 'monitoring'
  | 'state_condition'
  | 'ml_signal'
  | 'iot';

// ── Trigger Types ─────────────────────────────────────────────────────────────

// Mirrors the backend `app/triggers/models.py::TriggerType` enum EXACTLY (58
// values). The backend rejects any trigger_type outside this set with 422, so
// this union must stay in lock-step with it. Whether a type can actually be
// created/dispatched today is a separate concern — see SUPPORTED_TRIGGER_TYPES.
export type TriggerType =
  // A. Time / Schedule
  | 'cron'
  | 'interval'
  | 'once'
  | 'deadline'
  | 'relative_delay'
  | 'business_calendar'
  // B. Goal / Agent chain
  | 'goal_completed'
  | 'goal_failed'
  | 'goal_score_below'
  | 'hitl_approved'
  | 'hitl_rejected'
  | 'memory_created'
  // C. Conversational
  | 'chat_command'
  | 'chat_keyword'
  | 'chat_mention'
  | 'slack_event'
  | 'teams_webhook'
  | 'discord_event'
  | 'email_intent'
  | 'email_arrival'
  | 'sms_inbound'
  | 'voice_transcript'
  | 'meeting_ended'
  | 'form_submission'
  // D. External events / Webhooks
  | 'webhook'
  | 'rest'
  | 'event'
  | 'github_webhook'
  | 'jira_webhook'
  | 'stripe_webhook'
  | 'linear_webhook'
  | 'confluence_webhook'
  | 'salesforce_event'
  // E. Data sources
  | 'db_row_change'
  | 's3_event'
  | 'api_poll'
  | 'rss_feed'
  | 'file_drop'
  | 'google_sheets'
  | 'sharepoint'
  | 'graphql_subscription'
  | 'websocket_message'
  // F. Monitoring / Observability
  | 'cloudwatch'
  | 'grafana_alert'
  | 'sentry_issue'
  | 'alertmanager'
  | 'datadog'
  | 'pagerduty'
  | 'log_pattern'
  // G. State / Condition
  | 'state_transition'
  | 'condition'
  | 'counter_threshold'
  | 'compound'
  | 'window_aggregate'
  // H. Market / ML signal
  | 'price_threshold'
  // I. IoT
  | 'mqtt'
  | 'geofence'
  | 'sensor_threshold';

// ── TriggerSpec ───────────────────────────────────────────────────────────────

/**
 * Faithful mirror of the backend `app/triggers/models.py::TriggerSpec` dataclass.
 * Field NAMES must match the backend exactly — the API filters unknown keys, so a
 * drifted name (the old `run_at`/`condition_cel`/`webhook_secret`) silently fails
 * to round-trip. Keep this in lock-step with the backend spec.
 */
export interface TriggerSpec {
  trigger_id?: string;
  trigger_type: TriggerType;
  // ── Cross-cutting (all families) ──
  description?: string;
  goal_template?: string;
  condition?: string; // CEL expression gating any trigger type
  priority?: 'high' | 'normal' | 'low';
  max_firings_per_hour?: number; // 0 = unlimited (rate cap)
  expires_at_iso?: string; // auto-disable after this ISO datetime
  on_failure_notify?: string; // email/Slack channel on dispatch failure
  tags?: string[];
  // ── A: Time / Schedule ──
  cron_expression?: string;
  timezone?: string;
  interval_seconds?: number;
  fire_at_iso?: string;
  business_calendar_id?: string;
  relative_to_field?: string;
  relative_offset_seconds?: number;
  deadline_field?: string;
  deadline_warning_seconds?: number;
  // ── B: Goal / Agent chain ──
  watch_goal_id?: string;
  watch_agent_id?: string;
  score_threshold?: number;
  score_dimension?: string;
  hitl_queue_id?: string;
  memory_type?: string;
  // ── C: Conversational ──
  channel_type?: string;
  channel_id?: string;
  command_pattern?: string;
  keyword_pattern?: string;
  mention_bot_id?: string;
  email_sender_filter?: string;
  email_subject_pattern?: string;
  phone_number_filter?: string;
  voice_language?: string;
  meeting_platform?: string;
  form_id?: string;
  // ── D: Condition / State ──
  condition_expression?: string;
  counter_key?: string;
  counter_threshold?: number;
  counter_window_secs?: number;
  compound_logic?: 'AND' | 'OR';
  compound_trigger_ids?: string[];
  state_machine_id?: string;
  from_state?: string;
  to_state?: string;
  window_seconds?: number;
  window_field?: string;
  window_aggregation?: 'sum' | 'avg' | 'max' | 'min' | 'count';
  window_threshold?: number;
  // ── E: External events / Webhooks ──
  event_channel?: string;
  event_filter?: string;
  webhook_token?: string;
  webhook_signature_secret?: string;
  allowed_api_keys?: string[];
  github_event_filter?: string;
  jira_project_filter?: string;
  stripe_event_filter?: string;
  discord_server_id?: string;
  discord_channel_id?: string;
  salesforce_object?: string;
  teams_team_id?: string;
  teams_channel_id?: string;
  confluence_space_key?: string;
  linear_team_id?: string;
  // ── F: Data sources ──
  file_drop_path?: string;
  db_table?: string;
  db_operation?: string;
  db_filter?: string;
  rss_url?: string;
  sheets_spreadsheet_id?: string;
  sheets_range?: string;
  sharepoint_site_url?: string;
  sharepoint_library?: string;
  // ── G: Monitoring / Observability ──
  alert_severity_filter?: string;
  alert_labels?: Record<string, string>;
  log_pattern_regex?: string;
  log_stream?: string;
  cloudwatch_namespace?: string;
  cloudwatch_metric?: string;
  sentry_project?: string;
  sentry_environment?: string;
  // ── H: Polling / Streaming / Market ──
  poll_url?: string;
  poll_method?: string;
  poll_headers?: Record<string, string>;
  poll_body?: Record<string, unknown>;
  poll_jsonpath?: string;
  poll_expected_value?: string;
  poll_interval_seconds?: number;
  graphql_endpoint?: string;
  graphql_subscription_query?: string;
  websocket_url?: string;
  websocket_message_pattern?: string;
  price_symbol?: string;
  price_threshold?: number;
  price_direction?: 'above' | 'below';
  // ── I: IoT ──
  mqtt_broker_url?: string;
  mqtt_topic?: string;
  mqtt_qos?: number;
  geofence_polygon?: [number, number][];
  geofence_action?: 'enter' | 'exit' | 'both';
  sensor_device_id?: string;
  sensor_metric?: string;
  sensor_threshold?: number;
  sensor_comparison?: 'gt' | 'gte' | 'lt' | 'lte' | 'eq';
  // ── Governance / Misc ──
  allowed_roles?: string[];
  simulation_mode?: boolean;
  version?: number;
}

// ── Stored Trigger ────────────────────────────────────────────────────────────

export interface Trigger {
  schedule_id: string;
  goal_id: string;
  agent_id?: string;
  goal_template: string;
  spec: TriggerSpec;
  paused: boolean;
  created_at?: string;
  next_fire_at?: string;
  last_fired_at?: string;
  fire_count?: number;
}

// ── API Request / Response ────────────────────────────────────────────────────

export interface CreateTriggerRequest {
  spec: Omit<TriggerSpec, 'trigger_id'>;
  goal_id: string;
  agent_id?: string;
  goal_template: string;
}

export interface UpdateTriggerRequest {
  spec?: Partial<TriggerSpec>;
  goal_template?: string;
  paused?: boolean;
}

// ── Trigger Event ─────────────────────────────────────────────────────────────

export interface TriggerEvent {
  event_id: string;
  trigger_id: string;
  trigger_type: TriggerType;
  tenant_id: string;
  idempotency_key: string;
  payload: Record<string, unknown>;
  goal_id_created?: string;
  fired_at: string;
  simulated?: boolean;
}

// ── DLQ entry ─────────────────────────────────────────────────────────────────

export interface TriggerDLQEntry {
  id: string;
  trigger_id: string;
  failure_type: string;
  error_message: string;
  retry_count: number;
  next_retry_at?: string;
  created_at: string;
}

// ── Simulation result ─────────────────────────────────────────────────────────

export interface SimulationResult {
  trigger_type: TriggerType;
  sample_payload: Record<string, unknown>;
  would_fire: boolean;
  condition_result?: boolean;
  rate_limit_remaining?: number;
  circuit_breaker_state?: 'closed' | 'open' | 'half-open';
}

// ── Family metadata ───────────────────────────────────────────────────────────

export const TRIGGER_FAMILY_LABELS: Record<TriggerFamily, string> = {
  time: 'Time & Schedule',
  goal_chain: 'Goal Chain',
  conversational: 'Conversational',
  webhook: 'Webhooks',
  data: 'Data & Events',
  monitoring: 'Monitoring',
  state_condition: 'State & Condition',
  ml_signal: 'ML Signals',
  iot: 'IoT & Edge',
};

export const TRIGGER_TYPE_FAMILY: Record<TriggerType, TriggerFamily> = {
  // A. Time / Schedule
  cron: 'time', interval: 'time', once: 'time', deadline: 'time',
  relative_delay: 'time', business_calendar: 'time',
  // B. Goal / Agent chain
  goal_completed: 'goal_chain', goal_failed: 'goal_chain', goal_score_below: 'goal_chain',
  hitl_approved: 'goal_chain', hitl_rejected: 'goal_chain', memory_created: 'goal_chain',
  // C. Conversational
  chat_command: 'conversational', chat_keyword: 'conversational', chat_mention: 'conversational',
  slack_event: 'conversational', teams_webhook: 'conversational', discord_event: 'conversational',
  email_intent: 'conversational', email_arrival: 'conversational', sms_inbound: 'conversational',
  voice_transcript: 'conversational', meeting_ended: 'conversational', form_submission: 'conversational',
  // D. External events / Webhooks
  webhook: 'webhook', rest: 'webhook', event: 'webhook', github_webhook: 'webhook',
  jira_webhook: 'webhook', stripe_webhook: 'webhook', linear_webhook: 'webhook',
  confluence_webhook: 'webhook', salesforce_event: 'webhook',
  // E. Data sources
  db_row_change: 'data', s3_event: 'data', api_poll: 'data', rss_feed: 'data',
  file_drop: 'data', google_sheets: 'data', sharepoint: 'data',
  graphql_subscription: 'data', websocket_message: 'data',
  // F. Monitoring / Observability
  cloudwatch: 'monitoring', grafana_alert: 'monitoring', sentry_issue: 'monitoring',
  alertmanager: 'monitoring', datadog: 'monitoring', pagerduty: 'monitoring', log_pattern: 'monitoring',
  // G. State / Condition
  state_transition: 'state_condition', condition: 'state_condition',
  counter_threshold: 'state_condition', compound: 'state_condition', window_aggregate: 'state_condition',
  // H. Market / ML signal
  price_threshold: 'ml_signal',
  // I. IoT
  mqtt: 'iot', geofence: 'iot', sensor_threshold: 'iot',
};

/**
 * Trigger types that have a live runtime dispatch path today (backend
 * `dispatch_map.is_supported`). The create UI must offer only these — the other
 * enum values are recognised for parsing/round-trip but would 422 on create.
 * Keep in sync with the backend dispatch map.
 */
export const SUPPORTED_TRIGGER_TYPES: ReadonlySet<TriggerType> = new Set<TriggerType>([
  'cron', 'interval', 'once', 'deadline', 'relative_delay', 'business_calendar',
  'goal_completed', 'goal_failed', 'goal_score_below', 'hitl_approved', 'hitl_rejected', 'memory_created',
  'chat_command', 'chat_keyword', 'chat_mention', 'slack_event', 'teams_webhook', 'discord_event',
  'email_intent', 'email_arrival', 'sms_inbound', 'voice_transcript', 'meeting_ended', 'form_submission',
  'webhook', 'rest', 'event', 'github_webhook', 'jira_webhook', 'stripe_webhook', 'linear_webhook',
  'confluence_webhook', 'salesforce_event',
  'db_row_change', 'api_poll', 'rss_feed', 'file_drop',
  'cloudwatch', 'grafana_alert', 'sentry_issue', 'alertmanager', 'datadog', 'pagerduty',
  'state_transition', 'condition', 'counter_threshold', 'compound', 'window_aggregate',
]);
