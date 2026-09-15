// TypeScript types for the chat feature

export interface ChatSession {
  id: string;
  tenant_id: string;
  title: string;
  pinned: boolean;
  ttl_days: number | null;
  system_prompt: string | null;
  agent_id: string | null;
  folder_id: string | null;
  show_reasoning: boolean;
  proactive_suggestions: boolean;
  preferred_model: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata: Record<string, unknown>;
  intent: string | null;
  goal_id: string | null;
  branch_id: string | null;
  parent_message_id: string | null;
  created_at: string;
}

export interface ChatFolder {
  id: string;
  name: string;
  color: string;
  tenant_id?: string;
}

export interface ChatArtifact {
  id: string;
  title: string;
  language: string;
  content: string;
}

export interface ChatUsageSummary {
  session_id: string;
  total_tokens: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_usd: number;
  llm_calls: number;
}

export interface DispatchResult {
  intent: 'QA' | 'GOAL' | 'CLARIFY' | 'SCHEDULE';
  message_id: string;
  session_id: string;
  clarify_request: {
    question: string;
    options: string[];
    round: number;
  } | null;
  schedule_confirmation: {
    goal_text: string;
    cron_expression: string;
    human_schedule: string;
    next_run_iso: string | null;
  } | null;
}

// SSE event types — canonical wire vocabulary. Keep in lock-step with the
// backend contract in `agent-verse-backend/app/chat/events.py` (ChatEventType).
export type SSEEventType =
  // Lifecycle
  | 'message_started'
  | 'typing_started'          // back-compat alias for message_started
  | 'routing'                 // back-compat; superseded by intent_classified
  | 'intent_classified'
  | 'done'
  | 'error'
  // Generation
  | 'token'
  | 'reasoning'
  // Plan / execute / verify
  | 'plan_ready'
  | 'step_started'
  | 'step_complete'
  | 'tool_call'
  | 'tool_result'
  | 'knowledge_retrieved'
  | 'goal_complete'
  // Governance / safety
  | 'hitl_required'
  | 'hitl_resolved'
  | 'guardrail_blocked'
  | 'clarify_needed'
  // Async / platform
  | 'artifact_created'
  | 'schedule_created'
  | 'mission_started'
  // Telemetry
  | 'usage'
  | 'cost'
  // Diagnostics (back-compat)
  | 'failure_analysis'
  | 'proactive_suggestions';

export interface SSEEvent {
  type: SSEEventType;
  [key: string]: unknown;
}

export interface CreateSessionPayload {
  title?: string;
  system_prompt?: string;
  agent_id?: string;
  folder_id?: string;
  preferred_model?: string;
}

export interface UpdateSessionPayload {
  title?: string;
  system_prompt?: string;
  pinned?: boolean;
  folder_id?: string;
  show_reasoning?: boolean;
  proactive_suggestions?: boolean;
  preferred_model?: string;
}
