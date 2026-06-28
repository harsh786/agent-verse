/**
 * Typed API client for the AgentVerse backend.
 * Base URL is injected from environment — VITE_API_URL defaults to http://localhost:8000.
 * In development the Vite proxy rewrites /api → localhost:8000, but direct URL
 * works too and is required for production builds.
 */

import { useAuthStore } from '@/stores/auth';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

/** Exported alias for use in feature pages. */
export const API_BASE = API_BASE_URL;

// NOTE: sessionStorage is less vulnerable than localStorage (cleared on tab close)
// Production: use httpOnly cookie set by the backend auth endpoint
const getApiKey = (): string => {
  // Prefer the Zustand auth store (set via login/setCredentials actions)
  const storeKey = useAuthStore.getState().apiKey;
  if (storeKey) return storeKey;
  // Fall back to sessionStorage/localStorage for backward compat
  return (
    sessionStorage.getItem("av_api_key") ??
    localStorage.getItem("av_api_key") ?? // backward compat
    ""
  );
};

export const setApiKey = (key: string): void => {
  if (key) {
    sessionStorage.setItem("av_api_key", key);
    // Remove from localStorage (migration: don't persist API keys to disk)
    localStorage.removeItem("av_api_key");
  }
};

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (apiKey) headers["X-API-Key"] = apiKey;

  const res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { message: res.statusText } }));
    throw new ApiError(res.status, body?.error?.message ?? res.statusText, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Public alias for use in feature-level API modules (e.g. civilizationApi). */
export const apiFetch = request;

// ── Goals ────────────────────────────────────────────────────────────────────

export interface GoalRequest {
  goal: string;
  priority?: string;
  dry_run?: boolean;
  agent_id?: string;
  workflow_mode?: string;
}

export interface GoalResponse {
  id: string;
  goal_id?: string;
  status: string;
  goal: string;
  steps?: StepResponse[];
  iterations?: number;
  cost_usd?: number;
  created_at?: string;
}

export interface StepResponse {
  step_id: string;
  description: string;
  status: string;
  output: string;
}

// ── Goal extended types ───────────────────────────────────────────────────────

export interface GoalEvent {
  event_id: string;
  goal_id: string;
  type: string;
  payload?: Record<string, unknown>;
  created_at: string;
}

export interface EvalScorecard {
  goal_id: string;
  score: number;
  passed: boolean;
  criteria: Array<{ name: string; passed: boolean; score: number }>;
  evaluated_at: string;
}

export const goalsApi = {
  list: () => request<{ goals: GoalResponse[] }>("/goals"),
  submit: (body: GoalRequest) =>
    request<GoalResponse>("/goals", { method: "POST", body: JSON.stringify(body) }),
  get: (id: string) => request<GoalResponse>(`/goals/${id}`),
  cancel: (id: string) =>
    request<GoalResponse>(`/goals/${id}/cancel`, { method: "POST" }),
  submitBatch: (goals: string[], priority = "normal", agentId?: string) =>
    request<{ batch_id: string; total: number; goals: GoalResponse[] }>("/goals/batch", {
      method: "POST",
      body: JSON.stringify({ goals, priority, agent_id: agentId }),
    }),
  pause: (id: string) =>
    request<GoalResponse>(`/goals/${id}/pause`, { method: "POST" }),
  resume: (id: string) =>
    request<GoalResponse>(`/goals/${id}/resume`, { method: "POST" }),
  // Returns the persisted event log for a goal via the replay endpoint.
  // For real-time streaming use the useGoalStream hook (EventSource).
  getEventLog: (id: string) =>
    request<{ timeline: GoalEvent[] }>(`/goals/${id}/replay`)
      .then((data) => data?.timeline ?? []),
  getEvaluation: (id: string) =>
    request<EvalScorecard>(`/goals/${id}/eval`),
};

// ── Agents ───────────────────────────────────────────────────────────────────

export interface AgentResponse {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  goal_template?: string;
  status?: string;
  created_at?: string;
}

// ── Agent extended types ──────────────────────────────────────────────────────

export interface CreateAgentRequest {
  name: string;
  autonomy_mode: string;
  goal_template?: string;
  description?: string;
  tools?: string[];
  model?: string;
}

export interface AgentSnapshot {
  snapshot_id: string;
  agent_id: string;
  created_at: string;
  config: Record<string, unknown>;
}

export const agentsApi = {
  list: () => request<AgentResponse[]>("/agents"),
  get: (id: string) => request<AgentResponse>(`/agents/${id}`),
  create: (data: CreateAgentRequest) =>
    request<AgentResponse>("/agents", { method: "POST", body: JSON.stringify(data) }),
  createNl: (command: string, autorun = false) =>
    request<AgentResponse>("/agents/create", {
      method: "POST",
      body: JSON.stringify({ command, autorun }),
    }),
  update: (id: string, data: Partial<CreateAgentRequest>) =>
    request<AgentResponse>(`/agents/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  delete: (id: string) => request<void>(`/agents/${id}`, { method: "DELETE" }),
  snapshot: (id: string) =>
    request<AgentSnapshot>(`/agents/${id}/snapshot`, { method: "POST" }),
  listVersions: (id: string) => request<AgentSnapshot[]>(`/agents/${id}/versions`),
  rollback: (id: string, snapshotId: string) =>
    request<AgentResponse>(`/agents/${id}/rollback/${snapshotId}`, { method: "POST" }),
  export: (id: string, format: "openai" | "anthropic") =>
    request<object>(`/agents/${id}/export?format=${format}`),
};

// ── Connectors ────────────────────────────────────────────────────────────────

export interface ConnectorRequest {
  name: string;
  url: string;
  auth_type: string;
  auth_config: Record<string, string>;
  description?: string;
}

export interface ConnectorResponse {
  server_id: string;
  name: string;
  url: string;
  status?: string;
}

export interface CatalogEntry {
  name: string;
  description: string;
  auth_type: string;
  default_url: string;
}

// ── Connector extended types ──────────────────────────────────────────────────

export interface ConnectorTestResult {
  server_id: string;
  reachable: boolean;
  latency_ms?: number;
  error?: string;
}

export const connectorsApi = {
  catalog: () => request<CatalogEntry[]>("/connectors/catalog"),
  getCatalog: () => request<CatalogEntry[]>("/connectors/catalog"),
  list: () => request<ConnectorResponse[]>("/connectors"),
  register: (body: ConnectorRequest) =>
    request<ConnectorResponse>("/connectors", { method: "POST", body: JSON.stringify(body) }),
  unregister: (id: string) => request<void>(`/connectors/${id}`, { method: "DELETE" }),
  test: (id: string) =>
    request<ConnectorTestResult>(`/connectors/${id}/test`, { method: "POST" }),
};

// ── Tenants ───────────────────────────────────────────────────────────────────

export interface SignupRequest {
  name: string;
  email: string;
  plan?: string;
}

export interface TenantResponse {
  tenant_id: string;
  name: string;
  plan: string;
  raw_key?: string;
}

export interface ApiKeyResponse {
  key_id: string;
  name: string;
  created_at: string;
  last_used_at?: string;
}

export const tenantsApi = {
  signup: (body: SignupRequest) =>
    request<TenantResponse>("/tenants/signup", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<TenantResponse>("/tenants/me"),
  listKeys: () => request<ApiKeyResponse[]>("/tenants/me/keys"),
  createKey: (name: string) =>
    request<{ raw_key: string; key_id: string }>(
      "/tenants/me/keys",
      { method: "POST", body: JSON.stringify({ name }) }
    ),
  revokeKey: (keyId: string) =>
    request<void>(`/tenants/me/keys/${keyId}`, { method: "DELETE" }),
};

// ── Governance ────────────────────────────────────────────────────────────────

export interface ApprovalRequest {
  request_id: string;
  goal_id: string;
  action?: string;
  risk_level?: string;
  status: string;
}

export interface GoalMetrics {
  active_goals: number;
  total_goals: number;
  success_rate: number;
  avg_latency_ms: number;
  cost_today_usd: number;
  goals_today: number;
}

// ── Governance extended types ─────────────────────────────────────────────────

export interface Policy {
  policy_id: string;
  name: string;
  rule: string;
  enabled: boolean;
  created_at: string;
}

export interface CreatePolicyRequest {
  name: string;
  rule: string;
  enabled?: boolean;
}

export const governanceApi = {
  listApprovals: () => request<ApprovalRequest[]>("/governance/approvals"),
  approve: (requestId: string, approver: string, note: string) =>
    request<{ status: string }>(`/governance/approvals/${requestId}/approve`, {
      method: "POST",
      body: JSON.stringify({ approver, note }),
    }),
  reject: (requestId: string, approver: string, note: string) =>
    request<{ status: string }>(`/governance/approvals/${requestId}/reject`, {
      method: "POST",
      body: JSON.stringify({ approver, note }),
    }),
  goalMetrics: () => request<GoalMetrics>("/goals/metrics"),
  listPolicies: () => request<Policy[]>("/governance/policies"),
  createPolicy: (data: CreatePolicyRequest) =>
    request<Policy>("/governance/policies", { method: "POST", body: JSON.stringify(data) }),
  deletePolicy: (id: string) => request<void>(`/governance/policies/${id}`, { method: "DELETE" }),
  getPendingApprovals: () => request<ApprovalRequest[]>("/governance/hitl/pending"),
};

// ── Settings ──────────────────────────────────────────────────────────────────

export interface LLMConfig {
  provider: string;
  api_key: string;
  default_model?: string;
  base_url?: string;
}

export const settingsApi = {
  getLLM: () => request<LLMConfig>("/tenants/me/llm"),
  setLLM: (config: LLMConfig) =>
    request<LLMConfig>("/tenants/me/llm", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  listKeys: () => request<ApiKeyResponse[]>("/tenants/me/keys"),
  createKey: (name: string) =>
    request<{ raw_key: string; key_id: string }>("/tenants/me/keys", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  revokeKey: (keyId: string) =>
    request<void>(`/tenants/me/keys/${keyId}`, { method: "DELETE" }),
};

// ── Knowledge ────────────────────────────────────────────────────────────────

export interface KnowledgeCollection {
  collection_id: string;
  name: string;
  description?: string;
  document_count: number;
  created_at: string;
}

export interface IngestRequest {
  collection_id: string;
  content: string;
  source?: string;
  metadata?: Record<string, unknown>;
}

export interface SearchResult {
  document_id: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
}

export const knowledgeApi = {
  listCollections: () => request<KnowledgeCollection[]>("/knowledge/collections"),
  createCollection: (data: { name: string; description?: string }) =>
    request<KnowledgeCollection>("/knowledge/collections", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deleteCollection: (id: string) =>
    request<void>(`/knowledge/collections/${id}`, { method: "DELETE" }),
  ingest: (data: IngestRequest) =>
    request<void>("/knowledge/ingest", { method: "POST", body: JSON.stringify(data) }),
  search: (collectionId: string, query: string, limit = 10) =>
    request<SearchResult[]>(
      `/knowledge/search?collection_id=${collectionId}&q=${encodeURIComponent(query)}&limit=${limit}`
    ),
};

// ── Schedules ────────────────────────────────────────────────────────────────

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

export const schedulesApi = {
  list: () => request<Schedule[]>("/schedules"),
  create: (data: CreateScheduleRequest) =>
    request<Schedule>("/schedules", { method: "POST", body: JSON.stringify(data) }),
  delete: (id: string) => request<void>(`/schedules/${id}`, { method: "DELETE" }),
  createNl: (command: string) =>
    request<Schedule>("/nl/schedule", { method: "POST", body: JSON.stringify({ command }) }),
};

// ── Analytics ────────────────────────────────────────────────────────────────

export interface CostMetrics {
  total_cost_usd: number;
  cost_by_day: Array<{ date: string; cost_usd: number }>;
  cost_by_model: Record<string, number>;
  daily_budget_usd: number;
  budget_utilization: number;
}

export interface EvalMetrics {
  total_evals: number;
  pass_rate: number;
  avg_score: number;
  evals_by_day: Array<{ date: string; pass_rate: number }>;
}

export const analyticsApi = {
  getGoalMetrics: (days = 30) =>
    request<GoalMetrics>(`/analytics/goals?days=${days}`),
  getCostMetrics: (days = 30) =>
    request<CostMetrics>(`/analytics/costs?days=${days}`),
  getEvalMetrics: (days = 30) =>
    request<EvalMetrics>(`/analytics/evals?days=${days}`),
};

// ── Memory ───────────────────────────────────────────────────────────────────

export interface Memory {
  memory_id: string;
  content: string;
  tags?: string[];
  created_at: string;
}

export const memoryApi = {
  recall: (query: string, limit = 10) =>
    request<Memory[]>(`/memory/recall?q=${encodeURIComponent(query)}&limit=${limit}`),
  store: (data: { content: string; tags?: string[] }) =>
    request<Memory>("/memory", { method: "POST", body: JSON.stringify(data) }),
};
