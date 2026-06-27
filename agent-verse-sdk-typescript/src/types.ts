export interface Goal {
  goal_id: string;
  goal: string;
  status: 'planning' | 'executing' | 'verifying' | 'complete' | 'failed' | 'cancelled' | 'waiting_human';
  priority: string;
  dry_run: boolean;
  agent_id?: string;
  created_at?: string;
  event_count?: number;
}

export interface GoalEvent {
  type: string;
  step?: string;
  output?: string;
  success?: boolean;
  reason?: string;
  ts?: string;
  payload?: unknown;
  [key: string]: unknown;
}

export interface Agent {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  goal_template?: string;
  created_at?: string;
}

export interface Connector {
  server_id: string;
  name: string;
  url: string;
  status?: string;
}

export interface SubmitGoalOptions {
  priority?: string;
  dry_run?: boolean;
  agent_id?: string;
  persistence_mode?: boolean;
  workflow_mode?: string;
}

export interface CreateAgentRequest {
  name: string;
  goal_template?: string;
  autonomy_mode?: 'supervised' | 'bounded-autonomous' | 'fully-autonomous';
  connector_ids?: string[];
  trigger_config?: Record<string, any>;
  allowed_collection_ids?: string[];
  eval_suite_id?: string | null;
  policy_ids?: string[];
  system_prompt?: string;        // new — matches backend
  model_override?: string;       // new — renamed from model to match backend
  max_iterations?: number;       // new
  timeout_seconds?: number;      // new
}

export interface UpdateAgentRequest {
  name?: string;
  goal_template?: string;
  autonomy_mode?: string;
  connector_ids?: string[];
  system_prompt?: string;
  model_override?: string;
  max_iterations?: number;
  timeout_seconds?: number;
  allowed_collection_ids?: string[];
  eval_suite_id?: string | null;
  policy_ids?: string[];
}

export interface AgentSnapshot {
  snapshot_id: string;
  agent_id: string;
  created_at: string;
  config: Record<string, unknown>;
}

export interface Schedule {
  schedule_id: string;
  name: string;
  cron?: string;
  goal_template: string;
  enabled: boolean;
  next_run_at?: string;
  created_at: string;
}

export interface CreateScheduleRequest {
  name: string;
  cron: string;
  goal_template: string;
  agent_id?: string;
  enabled?: boolean;
}

export interface Memory {
  memory_id: string;
  content: string;
  tags?: string[];
  created_at: string;
}

export interface SearchResult {
  document_id: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
}

export interface ConnectorSpec {
  name: string;
  description: string;
  auth_type: string;
  default_url: string;
}

export interface ConnectorTestResult {
  server_id: string;
  reachable: boolean;
  latency_ms?: number;
  error?: string;
}

export interface GoalMetrics {
  active_goals: number;
  total_goals: number;
  success_rate: number;
  avg_latency_ms: number;
  cost_today_usd: number;
  goals_today: number;
}

export interface CostMetrics {
  total_cost_usd: number;
  cost_by_day: Array<{ date: string; cost_usd: number }>;
  cost_by_model: Record<string, number>;
  daily_budget_usd: number;
  budget_utilization: number;
}

export interface RolloutGateResult {
  gate_passed: boolean;
  reason: string;
  run_count: number;
  pass_rate: number;
  avg_score: number;
  min_pass_rate_required: number;
}

export interface ToolReliabilityStats {
  tool_name: string;
  success_count: number;
  failure_count: number;
  success_rate: number;
  avg_latency_ms: number;
  last_used_at: string | null;
}

export interface GoldenTask {
  task_id: string;
  goal: string;
  expected_output_contains: string;
  min_score: number;
  tags: string[];
}

export interface ConsentRecord {
  consent_id: string;
  purpose: string;
  status: string;
}

export interface GdprExportJob {
  job_id: string;
  status: string;
  poll_url?: string;
  download_url?: string;
}

export interface EvalScorecard {
  goal_id: string;
  score: number;
  passed: boolean;
  criteria: Array<{ name: string; passed: boolean; score: number }>;
  evaluated_at: string;
}

// Phase 20: Simulation and timeline types

export interface SimulationResult {
  run_id: string;
  goal: string;
  status: string;
  steps_executed: Array<{ description: string; tool: string; output: string }>;
  tools_called: string[];
  cost_estimate: number;
  used_real_llm: boolean;
}

export interface GoalTimeline {
  goal_id: string;
  goal_text: string;
  status: string;
  timeline: GoalEvent[];
  steps: Array<{ step_index: number; description: string; status: string; output?: string }>;
  evaluations: Array<{ scores: Record<string, number>; average_score: number }>;
}
