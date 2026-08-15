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

export type TriggerType =
  // A. Time family
  | 'cron'
  | 'interval'
  | 'one_shot'
  | 'calendar'
  | 'business_hours'
  | 'market_hours'
  | 'solar_event'
  | 'recurring_relative'
  | 'rate_limited_schedule'
  | 'deadline'
  // B. Goal chain
  | 'goal_completed'
  | 'goal_failed'
  | 'goal_score_below'
  | 'goal_score_above'
  | 'goal_timeout'
  | 'goal_created'
  | 'hitl_approved'
  | 'hitl_rejected'
  | 'memory_created'
  | 'goal_chain_depth'
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
  // D. Webhook
  | 'github_webhook'
  | 'jira_webhook'
  | 'stripe_webhook'
  | 'pagerduty_webhook'
  | 'linear_webhook'
  | 'custom_webhook'
  // E. Data
  | 'db_row_change'
  | 's3_event'
  | 'api_poll'
  | 'rss_feed'
  | 'kafka_message'
  | 'graphql_subscription'
  // F. Monitoring
  | 'metric_threshold'
  | 'log_pattern'
  | 'grafana_alert'
  | 'cloudwatch_alarm'
  | 'sentry_event'
  | 'uptime_check'
  // G. State/Condition
  | 'state_transition'
  | 'condition_true'
  | 'flag_change'
  | 'quota_exceeded'
  | 'cost_threshold'
  | 'user_segment'
  // H. ML signal
  | 'model_drift'
  | 'anomaly_detected'
  | 'prediction_confidence'
  | 'ab_test_winner'
  | 'price_movement'
  // I. IoT
  | 'mqtt'
  | 'geofence'
  | 'sensor_threshold';

// ── TriggerSpec ───────────────────────────────────────────────────────────────

export interface TriggerSpec {
  trigger_id: string;
  trigger_type: TriggerType;
  name?: string;
  description?: string;
  // Time
  cron_expression?: string;
  interval_seconds?: number;
  run_at?: string;
  // Goal chain
  watch_goal_id?: string;
  watch_agent_id?: string;
  score_threshold?: number;
  // Condition
  condition_cel?: string;
  // Template
  goal_template?: string;
  // Webhook
  webhook_secret?: string;
  webhook_url?: string;
  // MQTT
  mqtt_topic?: string;
  mqtt_broker_url?: string;
  mqtt_qos?: number;
  // Geofence
  geofence_polygon?: [number, number][];
  geofence_action?: 'enter' | 'exit' | 'both';
  // Misc
  enabled?: boolean;
  paused?: boolean;
  max_firings?: number;
  created_at?: string;
  updated_at?: string;
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
  cron: 'time', interval: 'time', one_shot: 'time', calendar: 'time',
  business_hours: 'time', market_hours: 'time', solar_event: 'time',
  recurring_relative: 'time', rate_limited_schedule: 'time', deadline: 'time',
  goal_completed: 'goal_chain', goal_failed: 'goal_chain', goal_score_below: 'goal_chain',
  goal_score_above: 'goal_chain', goal_timeout: 'goal_chain', goal_created: 'goal_chain',
  hitl_approved: 'goal_chain', hitl_rejected: 'goal_chain', memory_created: 'goal_chain',
  goal_chain_depth: 'goal_chain',
  chat_command: 'conversational', chat_keyword: 'conversational', chat_mention: 'conversational',
  slack_event: 'conversational', teams_webhook: 'conversational', discord_event: 'conversational',
  email_intent: 'conversational', email_arrival: 'conversational', sms_inbound: 'conversational',
  voice_transcript: 'conversational', meeting_ended: 'conversational', form_submission: 'conversational',
  github_webhook: 'webhook', jira_webhook: 'webhook', stripe_webhook: 'webhook',
  pagerduty_webhook: 'webhook', linear_webhook: 'webhook', custom_webhook: 'webhook',
  db_row_change: 'data', s3_event: 'data', api_poll: 'data', rss_feed: 'data',
  kafka_message: 'data', graphql_subscription: 'data',
  metric_threshold: 'monitoring', log_pattern: 'monitoring', grafana_alert: 'monitoring',
  cloudwatch_alarm: 'monitoring', sentry_event: 'monitoring', uptime_check: 'monitoring',
  state_transition: 'state_condition', condition_true: 'state_condition', flag_change: 'state_condition',
  quota_exceeded: 'state_condition', cost_threshold: 'state_condition', user_segment: 'state_condition',
  model_drift: 'ml_signal', anomaly_detected: 'ml_signal', prediction_confidence: 'ml_signal',
  ab_test_winner: 'ml_signal', price_movement: 'ml_signal',
  mqtt: 'iot', geofence: 'iot', sensor_threshold: 'iot',
};
