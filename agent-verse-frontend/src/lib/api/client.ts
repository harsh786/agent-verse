/**
 * Typed API client for the AgentVerse backend.
 * Base URL is injected from environment — VITE_API_URL defaults to http://localhost:8000.
 * In development the Vite proxy rewrites /api → localhost:8000, but direct URL
 * works too and is required for production builds.
 */

import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import type { ResultArtifact } from '@/features/goals/resultArtifact';

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
  const { ssoMode, accessToken } = useAuthStore.getState();
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  // SSO mode: send Keycloak JWT as a Bearer token; the backend middleware
  // validates it and resolves the tenant without an API key.
  if (ssoMode && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  } else if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch (networkErr) {
    toast({ kind: 'error', message: 'Network error — could not reach the server.' });
    throw networkErr;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { message: res.statusText } }));
    const message = body?.error?.message ?? res.statusText;
    if (res.status === 401) {
      const { logout } = useAuthStore.getState();
      logout();
      toast({ kind: 'error', message: 'Session expired — please sign in again.' });
      throw new ApiError(401, message, body);
    }
    if (res.status >= 500) toast({ kind: 'error', message: `Server error: ${message}` });
    throw new ApiError(res.status, message, body);
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
  result_artifact?: ResultArtifact;
}

export interface StepResponse {
  step_id: string;
  description: string;
  status: string;
  output: string;
}

/**
 * Token-level streaming event emitted by the backend during executor LLM calls.
 * These events are ephemeral — they are NOT stored in the event log.
 */
export interface TokenChunkEvent {
  type: 'token_chunk';
  /** Step description that is currently being executed. */
  step: string;
  /** The individual token fragment just emitted by the LLM. */
  token: string;
  /** Full text accumulated so far for this step (token1 + token2 + …). */
  cumulative: string;
  /** ISO-8601 timestamp added by the backend. */
  ts?: string;
}

// ── Goal extended types ───────────────────────────────────────────────────────

export interface GoalEvent {
  event_id?: string;
  goal_id?: string;
  type: string;
  payload?: Record<string, unknown>;
  data?: Record<string, unknown>;
  created_at?: string;
  ts?: string;
}

export interface EvalScorecard {
  goal_id: string;
  score?: number;
  average_score?: number;
  passed: boolean;
  criteria?: Array<{ name: string; passed: boolean; score: number }>;
  evaluated_at?: string;
  status?: string;
  scores?: {
    task_completion?: number;
    efficiency?: number;
    accuracy?: number;
    safety?: number;
    coherence?: number;
    sla?: number;
    tool_relevance?: number;
    [key: string]: number | undefined;
  };
  iterations?: number;
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
  triggerEvaluation: (id: string) =>
    request<EvalScorecard>(`/goals/${id}/eval`, { method: "POST" }),
};

// ── Agents ───────────────────────────────────────────────────────────────────

export interface AgentResponse {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  goal_template?: string;
  status?: string;
  created_at?: string;
  // Extended fields returned by the backend but not always present
  max_iterations?: number;
  model_override?: string;
  system_prompt?: string;
  connector_ids?: string[];
  allowed_collection_ids?: string[];
  description?: string;
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
  // Phase-5 additions
  getPermissions: (id: string) =>
    request<{ read: string[]; write: string[] }>(`/agents/${id}/permissions`),
  clone: (id: string) =>
    request<AgentResponse>(`/agents/${id}/clone`, { method: "POST" }),
  assignKnowledge: (agentId: string, knowledgeId: string) =>
    request<void>(`/agents/${agentId}/knowledge/${knowledgeId}`, { method: "POST" }),
  removeKnowledge: (agentId: string, knowledgeId: string) =>
    request<void>(`/agents/${agentId}/knowledge/${knowledgeId}`, { method: "DELETE" }),
  getRolloutGate: (id: string) =>
    request<{ gate_status: string; traffic_pct: number; conditions: string[] }>(
      `/agents/${id}/rollout-gate`
    ),
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

export interface CatalogAuthField {
  key: string;
  label: string;
  placeholder: string;
  field_type: 'text' | 'password' | 'url' | 'email';
  required: boolean;
  hint: string;
}

export interface CatalogEntry {
  name: string;
  display_name: string;
  description: string;
  auth_type: string;
  default_url: string;
  icon: string;
  category: string;
  auth_fields: CatalogAuthField[];
  has_builtin: boolean;
  builtin_server_id: string;
  is_configured: boolean;
  connector_type: string;
}

// ── Connector extended types ──────────────────────────────────────────────────

export interface ConnectorTestResult {
  server_id: string;
  reachable: boolean;
  latency_ms?: number;
  error?: string;
}

export const connectorsApi = {
  getCatalog: () => request<CatalogEntry[]>("/connectors/catalog"),
  list: () => request<ConnectorResponse[]>("/connectors"),
  register: (body: ConnectorRequest) =>
    request<ConnectorResponse>("/connectors", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: Partial<ConnectorRequest>) =>
    request<ConnectorResponse>(`/connectors/${id}`, { method: "PUT", body: JSON.stringify(body) }),
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
  scopes?: string[];
  created_at: string;
  last_used_at?: string;
  expires_at?: string | null;
}

export const tenantsApi = {
  signup: (body: SignupRequest) =>
    request<TenantResponse>("/tenants/signup", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<TenantResponse>("/tenants/me"),
  listKeys: () => request<ApiKeyResponse[]>("/tenants/me/keys"),
  createKey: (name: string, scopes?: string[]) =>
    request<{ raw_key: string; key_id: string }>(
      "/tenants/me/keys",
      { method: "POST", body: JSON.stringify({ name, scopes: scopes ?? [] }) }
    ),
  revokeKey: (keyId: string) =>
    request<void>(`/tenants/me/keys/${keyId}`, { method: "DELETE" }),
  rotateKey: (keyId: string) =>
    request<{ raw_key: string; key_id: string }>(
      `/tenants/me/keys/${keyId}/rotate`,
      { method: "POST", body: JSON.stringify({ revoke_old: true }) }
    ),
};

// ── Governance ────────────────────────────────────────────────────────────────

export interface ApprovalRequest {
  request_id: string;
  goal_id: string;
  action?: string;
  risk_level?: string;
  status: string;
  // Extended fields returned by the world-class backend
  created_at?: string;
  resolved_at?: string;
  note?: string;
  approver?: string | null;
  required_approvers?: number;
  approvals_received?: number;
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

/** Legacy Policy shape (kept for backward compat with existing code) */
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

/** Governance Policy — matches backend /governance/policies response */
export interface GovernancePolicy {
  policy_id: string;
  name: string;
  description: string;
  tools_pattern: string;
  action: "deny" | "require_approval";
  priority: number;
  allowed_hours_utc?: number[];
  allowed_weekdays?: number[];
}

export interface CreateGovernancePolicyRequest {
  name: string;
  description?: string;
  tools_pattern: string;
  action: "deny" | "require_approval";
  priority?: number;
  allowed_hours_utc?: number[];
  allowed_weekdays?: number[];
}

export interface PolicySimulateResult {
  simulation_results: Record<string, string>;
  tenant_id: string;
}

export interface PolicyVersion {
  id: string;
  policy_id: string;
  version_number: number;
  name: string;
  description: string | null;
  is_active: boolean;
  change_summary: string | null;
  changed_by: string | null;
  changed_at: string;
}

export interface SlaStats {
  pending: number;
  approved: number;
  denied: number;
  timed_out: number;
  escalated: number;
  within_sla: number;
  avg_resolution_seconds: number;
}

export interface AuditChainResult {
  verified: boolean;
  verified_events: number;
  broken_chain_at?: string;
  chain_tip_hash?: string;
}

export interface GovBudget {
  tenant_id: string;
  per_goal_usd: number;
  per_tenant_daily_usd: number;
}

export interface BatchApproveResult {
  approved: number;
  rejected: number;
  not_found: number;
  results: Array<{ request_id: string; result: string }>;
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
  batchApprove: (requestIds: string[], action: "approve" | "reject", approver: string, note = "") =>
    request<BatchApproveResult>("/governance/hitl/batch-approve", {
      method: "POST",
      body: JSON.stringify({ request_ids: requestIds, action, approver, note }),
    }),
  getSlaStats: () => request<SlaStats>("/governance/approvals/sla-stats"),
  listHistory: (limit = 50, statusFilter?: string) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (statusFilter) qs.set("status_filter", statusFilter);
    return request<ApprovalRequest[]>(`/governance/approvals/history?${qs.toString()}`);
  },
  goalMetrics: () => request<GoalMetrics>("/goals/metrics"),
  // Policies (correct shape matching backend)
  listGovernancePolicies: () => request<GovernancePolicy[]>("/governance/policies"),
  createGovernancePolicy: (data: CreateGovernancePolicyRequest) =>
    request<GovernancePolicy>("/governance/policies", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deletePolicy: (id: string) => request<void>(`/governance/policies/${id}`, { method: "DELETE" }),
  simulatePolicies: (toolCalls: string[]) =>
    request<PolicySimulateResult>("/governance/policies/simulate", {
      method: "POST",
      body: JSON.stringify({ tool_calls: toolCalls }),
    }),
  getPolicyVersions: (policyId: string) =>
    request<PolicyVersion[]>(`/governance/policies/${policyId}/versions`),
  rollbackPolicy: (policyId: string, targetVersion: number, reason: string) =>
    request<{ policy_id: string; new_version: number; rolled_back_to: number; reason: string }>(
      `/governance/policies/${policyId}/rollback`,
      { method: "POST", body: JSON.stringify({ target_version: targetVersion, reason }) }
    ),
  // Budget
  getBudget: () => request<GovBudget>("/governance/budget"),
  setBudget: (data: { per_goal_usd: number; per_tenant_daily_usd: number }) =>
    request<GovBudget>("/governance/budget", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  // Audit chain integrity
  verifyAuditChain: () => request<AuditChainResult>("/governance/audit/integrity/verify"),
  // Legacy — kept for backward compat
  listPolicies: () => request<Policy[]>("/governance/policies"),
  createPolicy: (data: CreatePolicyRequest) =>
    request<Policy>("/governance/policies", { method: "POST", body: JSON.stringify(data) }),
  getPendingApprovals: () => request<ApprovalRequest[]>("/governance/hitl/pending"),
  approvalsStreamPath: () => "/governance/approvals/stream",
  policiesStreamPath: () => "/governance/policies/stream",
  emergencyStop: () =>
    request<{ status: string; cancelled_goals: number; rejected_approvals: number }>(
      "/governance/emergency-stop",
      { method: "POST" }
    ),
  clearEmergencyStop: () =>
    request<{ status: string; tenant_id: string }>(
      "/governance/emergency-stop",
      { method: "DELETE" }
    ),
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
  list: () => request<KnowledgeCollection[]>("/knowledge/collections"),
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

export interface MemoryEntry {
  id: string;
  content: string;
  memory_type: string;
  confidence: number;
  tags: string[];
  created_at: string;
}

export interface RecallResult {
  content: string;
  confidence: number;
  memory_type: string;
  source: string;
}

export interface ToolReliabilityRow {
  tool_name: string;
  success_count: number;
  failure_count: number;
  total_calls: number;
  success_rate: number;
  [key: string]: unknown;
}

export const memoryApi = {
  list: (opts: { limit?: number; memoryType?: string } = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(opts.limit ?? 50));
    if (opts.memoryType) params.set("memory_type", opts.memoryType);
    return request<MemoryEntry[]>(`/memory?${params.toString()}`);
  },
  recall: (query: string, limit = 10) =>
    request<{ query: string; results: RecallResult[] }>(
      `/memory/recall?q=${encodeURIComponent(query)}&limit=${limit}`
    ).then((d) => d.results ?? []),
  create: (data: { content: string; memory_type?: string; confidence?: number; tags?: string[] }) =>
    request<MemoryEntry>("/memory", { method: "POST", body: JSON.stringify(data) }),
  delete: (id: string) =>
    request<{ deleted: string; status: string }>(`/memory/${id}`, { method: "DELETE" }),
  clearAll: () => request<void>("/memory", { method: "DELETE" }),
  toolReliability: () => request<ToolReliabilityRow[]>("/memory/tool-reliability"),
  listExecution: () => request<Array<{ goal_text: string; success: boolean; recorded_at: string }>>("/memory/execution"),
};

// ── Artifacts ──────────────────────────────────────────────────────────────────

export interface Artifact {
  id: string;
  name: string;
  artifact_type: string;
  storage_uri: string;
  content_type: string;
  size_bytes: number;
  goal_id: string;
  created_at: string;
}

export const artifactsApi = {
  list: (opts: { goalId?: string; artifactType?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.goalId) params.set("goal_id", opts.goalId);
    if (opts.artifactType) params.set("artifact_type", opts.artifactType);
    params.set("limit", String(opts.limit ?? 50));
    return request<Artifact[]>(`/artifacts?${params.toString()}`);
  },
  get: (id: string) => request<Artifact>(`/artifacts/${id}`),
  delete: (id: string) => request<void>(`/artifacts/${id}`, { method: "DELETE" }),
};

// ── Tools ──────────────────────────────────────────────────────────────────────

const encodePath = (p: string): string =>
  p.split("/").map(encodeURIComponent).join("/");

export interface ExecuteCodeResult {
  stdout: string;
  stderr: string;
  exit_code: number;
  success: boolean;
  timed_out: boolean;
  execution_time_ms: number;
}

export interface WorkspaceFile {
  name: string;
  path: string;
  type?: "file" | "directory";
  is_dir?: boolean;
  size_bytes?: number;
  modified_at?: number;
  [key: string]: unknown;
}

export const toolsApi = {
  executeCode: (
    code: string,
    language: "python" | "javascript" | "bash" = "python",
    timeout = 30
  ) =>
    request<ExecuteCodeResult>("/tools/execute-code", {
      method: "POST",
      body: JSON.stringify({ code, language, timeout }),
    }),
  listFiles: (directory = ".") =>
    request<WorkspaceFile[]>(`/tools/files?directory=${encodeURIComponent(directory)}`),
  readFile: (path: string) =>
    request<{ path: string; content: string; success: boolean }>(`/tools/files/${encodePath(path)}`),
  writeFile: (path: string, content: string) =>
    request<{ path: string; bytes_written: number; success: boolean }>(
      `/tools/files/${encodePath(path)}`,
      { method: "POST", body: JSON.stringify({ content }) }
    ),
  deleteFile: (path: string) =>
    request<void>(`/tools/files/${encodePath(path)}`, { method: "DELETE" }),
  sendEmail: (body: {
    to: string | string[];
    subject: string;
    body: string;
    from_addr?: string;
  }) =>
    request<Record<string, unknown>>("/tools/email/send", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ── Training export ──────────────────────────────────────────────────────────

function parseFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const match = /filename="?([^"]+)"?/.exec(disposition);
  return match ? match[1] : fallback;
}

export interface TrainingPreview {
  count: number;
  avg_score: number;
  min_score_found: number;
  max_score_found: number;
  score_distribution: Record<string, number>;
  samples: Array<{ goal: string; eval_score: number; steps: number; tools: string[] }>;
}

export const trainingApi = {
  export: async (opts: {
    format: "openai" | "anthropic";
    minScore?: number;
    limit?: number;
  }): Promise<{ blob: Blob; filename: string; count: number }> => {
    const params = new URLSearchParams();
    params.set("format", opts.format);
    params.set("min_score", String(opts.minScore ?? 0.8));
    params.set("limit", String(opts.limit ?? 1000));
    const apiKey = getApiKey();
    const headers: Record<string, string> = {};
    if (apiKey) headers["X-API-Key"] = apiKey;
    const res = await fetch(
      `${API_BASE_URL}/intelligence/export-training-data?${params.toString()}`,
      { method: "POST", headers }
    );
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    const blob = await res.blob();
    return {
      blob,
      filename: parseFilename(
        res.headers.get("Content-Disposition"),
        `training_${opts.format}.jsonl`
      ),
      count: Number(res.headers.get("X-Training-Examples") ?? 0),
    };
  },

  preview: (opts: { minScore?: number; limit?: number }): Promise<TrainingPreview> => {
    const params = new URLSearchParams();
    params.set("min_score", String(opts.minScore ?? 0.8));
    params.set("limit", String(opts.limit ?? 1000));
    return request<TrainingPreview>(
      `/intelligence/export-training-data/preview?${params.toString()}`
    );
  },
};

// ── Perception ─────────────────────────────────────────────────────────────────

export interface PerceptionStatus {
  playwright_available: boolean;
  vision_available: boolean;
  browser_actions: string[];
  image_formats: string[];
}

export interface BatchAnalysisResult {
  url: string;
  success: boolean;
  analysis: string;
  screenshot_b64: string;
  text_content: string;
  error: string | null;
}

export const perceptionApi = {
  status: () => request<PerceptionStatus>("/perception/status"),
  screenshot: (url: string, fullPage = false) =>
    request<{ success: boolean; url: string; screenshot_b64: string; error: string | null }>(
      "/perception/screenshot",
      { method: "POST", body: JSON.stringify({ url, full_page: fullPage }) }
    ),
  analyze: (body: { screenshot_b64?: string; url?: string; question?: string }) =>
    request<{ analysis: string; question: string; screenshot_provided: boolean }>(
      "/perception/analyze",
      { method: "POST", body: JSON.stringify(body) }
    ),
  extract: (url: string, selector = "body") =>
    request<{
      success: boolean;
      url: string;
      selector: string;
      text: string;
      char_count: number;
      error: string | null;
    }>("/perception/extract", {
      method: "POST",
      body: JSON.stringify({ url, selector }),
    }),
  batchAnalyze: (urls: string[], question?: string) =>
    request<{ results: BatchAnalysisResult[]; total: number; succeeded: number }>(
      "/perception/batch-analyze",
      { method: "POST", body: JSON.stringify({ urls, question }) }
    ),
  submitGoalWithImage: (body: {
    goal: string;
    image_b64?: string;
    image_url?: string;
    image_description?: string;
    priority?: string;
    dry_run?: boolean;
    agent_id?: string | null;
  }) =>
    request<{ goal_id: string; has_visual_context: boolean; original_goal: string }>(
      "/perception/goal-with-image",
      { method: "POST", body: JSON.stringify(body) }
    ),
};

// ── A2A (read-only) ──────────────────────────────────────────────────────────

export interface AgentCard {
  agent_id: string;
  name: string;
  version: string;
  description: string;
  endpoint: string;
  authentication: { scheme: string; header: string; note: string };
  capabilities: string[];
  supported_task_types: string[];
}

export interface A2ATask {
  task_id: string;
  goal: string;
  status: string;
  result?: string;
  callback_url?: string;
  requester_agent_id?: string;
  created_at?: string;
}

export interface A2ATaskSubmit {
  goal: string;
  context?: Record<string, unknown>;
  callback_url?: string;
  requester_agent_id?: string;
  priority?: string;
}

export const a2aApi = {
  agentCard: () => request<AgentCard>("/.well-known/agent.json"),
  getTask: (taskId: string) => request<A2ATask>(`/a2a/tasks/${taskId}`),
  listTasks: (limit = 50) => request<A2ATask[]>(`/a2a/tasks?limit=${limit}`),
  submitTask: (data: A2ATaskSubmit) =>
    request<{ task_id: string; status: string; message: string }>("/a2a/tasks", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

// ── RPA ───────────────────────────────────────────────────────────────────────

export interface RpaSession {
  session_id: string;
  status: "active" | "paused" | "closed" | string;
  created_at: string;
  last_used_at?: string;
}

export interface RpaTool {
  name: string;
  description: string;
  risk: "low" | "high" | "read" | string;
  input_schema?: Record<string, unknown>;
}

export interface RpaExecuteResult {
  success: boolean;
  output: string;
  artifact_url?: string;
  artifact_name?: string;
  duration_ms?: number;
  error?: string;
  tool_name: string;
  session_id?: string;
}

export interface RpaScreenshot {
  session_id: string;
  screenshot_data_uri: string;
  url?: string;
  timestamp?: string;
}

export const rpaApi = {
  listSessions: () => request<RpaSession[]>("/rpa/sessions"),
  createSession: () => request<RpaSession>("/rpa/sessions", { method: "POST" }),
  deleteSession: (id: string) => request<void>(`/rpa/sessions/${id}`, { method: "DELETE" }),
  getScreenshot: (id: string) => request<RpaScreenshot>(`/rpa/sessions/${id}/screenshot`),
  takeover: (id: string, reason: string) =>
    request<{ session_id: string; status: string; live_url?: string; message: string }>(
      `/rpa/sessions/${id}/takeover`,
      { method: "POST", body: JSON.stringify({ reason }) }
    ),
  listTools: () => request<{ tools: RpaTool[] }>("/rpa/tools"),
  execute: (toolName: string, args: Record<string, unknown>, sessionId?: string) =>
    request<RpaExecuteResult>("/rpa/execute", {
      method: "POST",
      body: JSON.stringify({ tool_name: toolName, arguments: args, session_id: sessionId }),
    }),
};

// ── Integrations (inbound webhooks; config + delivery visibility) ──────────────

export interface ZapierCompletedGoal {
  id?: string;
  goal_id?: string;
  goal?: string;
  status: string;
  [key: string]: unknown;
}

export const integrationsApi = {
  zapierCompletedGoals: () =>
    request<ZapierCompletedGoal[]>("/integrations/zapier/goals"),
};

// ── Governance real-time helpers + Audit ──────────────────────────────────────

export interface AuditEvent {
  event_id: string;
  goal_id: string;
  tool_name: string;
  action_level: string;
  outcome: string;
  step_id?: string;
  approver?: string;
  note?: string;
}

export interface AuditQuery {
  goal_id?: string;
  tool_name?: string;
  limit?: number;
  offset?: number;
  start_time?: string;
  end_time?: string;
}

export const auditApi = {
  query: (q: AuditQuery = {}) => {
    const params = new URLSearchParams();
    if (q.goal_id) params.set("goal_id", q.goal_id);
    if (q.tool_name) params.set("tool_name", q.tool_name);
    params.set("limit", String(q.limit ?? 200));
    if (q.offset) params.set("offset", String(q.offset));
    if (q.start_time) params.set("start_time", q.start_time);
    if (q.end_time) params.set("end_time", q.end_time);
    return request<AuditEvent[]>(`/governance/audit?${params.toString()}`);
  },
};

// ── Notifications ──────────────────────────────────────────────────────────────

export interface NotificationChannel {
  channel_id: string;
  type: string;
  enabled: boolean;
}

export interface CreateNotificationChannelRequest {
  channel_type: string; // "slack" | "webhook" | "teams"
  config: Record<string, unknown>;
}

 export const notificationsApi = {
  list: () => request<NotificationChannel[]>("/governance/notifications"),
  create: (body: CreateNotificationChannelRequest) =>
    request<{ channel_id: string; type: string; status: string }>(
      "/governance/notifications",
      { method: "POST", body: JSON.stringify(body) },
    ),
  delete: (channelId: string) =>
    request<void>(`/governance/notifications/${channelId}`, { method: "DELETE" }),
  /** Send a test notification to verify channel connectivity */
  test: (channelId: string) =>
    request<{ success: boolean; message: string }>(
      `/governance/notifications/${channelId}/test`,
      { method: "POST" }
    ),
};

// ── RBAC: roles + IP allowlist ─────────────────────────────────────────────────

export interface RoleAssignment {
  id: string;
  user_id: string;
  role: string;
  created_at?: string;
}

export interface IpAllowlistEntry {
  id: string;
  cidr: string;
  description: string;
  created_at?: string;
}

export const rbacApi = {
  listRoles: () => request<RoleAssignment[]>("/tenants/me/roles"),
  createRole: (userId: string, role: string) =>
    request<RoleAssignment>("/tenants/me/roles", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, role }),
    }),
  deleteRole: (roleId: string) =>
    request<void>(`/tenants/me/roles/${roleId}`, { method: "DELETE" }),
  listIpAllowlist: () => request<IpAllowlistEntry[]>("/tenants/me/ip-allowlist"),
  addIpAllowlist: (cidr: string, description = "") =>
    request<IpAllowlistEntry>("/tenants/me/ip-allowlist", {
      method: "POST",
      body: JSON.stringify({ cidr, description }),
    }),
  deleteIpAllowlist: (entryId: string) =>
    request<void>(`/tenants/me/ip-allowlist/${entryId}`, { method: "DELETE" }),
};

// ── Compliance: legal hold + GDPR export + consent ─────────────────────────────

export interface LegalHold {
  id: string;
  reason: string;
  expires_at: string | null;
  created_by: string;
}

export interface GdprExportJob {
  job_id: string;
  status: string; // "pending" | "running" | "complete" | "failed"
  completed_at: string | null;
  download_url: string | null;
  error: string | null;
}

export interface ConsentRecord {
  consent_id: string;
  purpose: string;
  status: string;
}

// ── Compliance extended types ─────────────────────────────────────────────────

export interface ComplianceFrameworkStatus {
  framework: string;
  compliant: boolean;
  checks: Array<{ check: string; passed: boolean; detail?: string }>;
  tenant_id: string;
}

export interface DataResidency {
  region: string;
  provider: string;
  data_types: string[];
}

export interface Contract {
  contract_id?: string;
  contract_type: string;
  status: string;   // "pending_signature" | "signed"
  signed_by?: string;
  signed_at?: string;
}

export const complianceApi = {
  listLegalHolds: () => request<LegalHold[]>("/governance/legal-holds"),
  createLegalHold: (reason: string, expiresAt?: string) =>
    request<{ status: string; tenant_id: string; reason: string }>(
      "/governance/legal-hold",
      { method: "POST", body: JSON.stringify({ reason, expires_at: expiresAt ?? null }) }
    ),
  startGdprExport: () =>
    request<{ job_id: string; status: string; poll_url: string }>(
      "/compliance/export/start",
      { method: "POST" },
    ),
  getGdprExportStatus: (jobId: string) =>
    request<GdprExportJob>(`/compliance/export/jobs/${jobId}`),
  recordConsent: (purpose: string, legalBasis = "legitimate_interest") =>
    request<ConsentRecord>(
      "/compliance/consent",
      { method: "POST", body: JSON.stringify({ purpose, legal_basis: legalBasis }) },
    ),
  revokeConsent: (purpose: string) =>
    request<{ purpose: string; status: string }>(
      `/compliance/consent/${purpose}`,
      { method: "DELETE" },
    ),
  getFrameworkStatus: (framework: "gdpr" | "hipaa" | "soc2") =>
    request<ComplianceFrameworkStatus>(`/enterprise/compliance/${framework}`),
  runComplianceCheck: (framework: "gdpr" | "hipaa" | "soc2") =>
    request<ComplianceFrameworkStatus>(`/enterprise/compliance/${framework}/check`, { method: "POST" }),
  getResidency: () => request<DataResidency>("/enterprise/compliance/residency"),
  listContracts: () =>
    request<Contract[] | { contracts: Contract[] }>("/enterprise/contracts").then(
      (r) => Array.isArray(r) ? r : (r as { contracts: Contract[] }).contracts ?? []
    ),
  signContract: (contractType: string, signerName: string, signerEmail: string) =>
    request<Contract>(`/enterprise/contracts/${contractType}/sign`, {
      method: "POST",
      body: JSON.stringify({ signer_name: signerName, signer_email: signerEmail }),
    }),
};

// ── Eval Suites (Phase-5) ─────────────────────────────────────────────────────

export interface EvalSuite {
  suite_id: string;
  name: string;
  description?: string;
  task_count: number;
  created_at: string;
}

export interface EvalSuiteResult {
  run_id: string;
  suite_id: string;
  overall_score: number;
  passed: number;
  failed: number;
  completed_at: string;
}

export const evalSuitesApi = {
  listSuites: () => request<EvalSuite[]>("/intelligence/eval-suites"),
  createSuite: (name: string, description?: string) =>
    request<EvalSuite>("/intelligence/eval-suites", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  getSuite: (id: string) => request<EvalSuite>(`/intelligence/eval-suites/${id}`),
  addTask: (suiteId: string, task: { input: string; expected_output?: string; tags?: string[] }) =>
    request<void>(`/intelligence/eval-suites/${suiteId}/tasks`, {
      method: "POST",
      body: JSON.stringify(task),
    }),
  runSuite: (id: string) =>
    request<{ run_id: string }>(`/intelligence/eval-suites/${id}/run`, { method: "POST" }),
  getSuiteResults: (id: string) =>
    request<EvalSuiteResult[]>(`/intelligence/eval-suites/${id}/results`),
};

// ── Workflows (Phase-6) ────────────────────────────────────────────────────────

export interface WorkflowRecord {
  id: string;
  name: string;
  description?: string;
  definition?: Record<string, unknown>;
  status: string;
  version?: number;
  created_at?: string;
}

export const workflowsApi = {
  list: () => request<WorkflowRecord[]>("/workflows"),
  get: (id: string) => request<WorkflowRecord>(`/workflows/${id}`),
  create: (data: { name: string; description?: string; definition?: object }) =>
    request<WorkflowRecord>("/workflows", { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: { name: string; description?: string; definition?: object }) =>
    request<void>(`/workflows/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  delete: (id: string) => request<void>(`/workflows/${id}`, { method: "DELETE" }),
  run: (id: string, dryRun = false) =>
    request<{ run_id: string; status: string }>(
      `/workflows/${id}/run${dryRun ? "?dry_run=true" : ""}`,
      { method: "POST" }
    ),
};

// ── Simulation (governance sandbox) ──────────────────────────────────────────

export interface SimulationSummary {
  allowed_tools: string[];
  denied_tools: string[];
  requires_approval: string[];
  would_block_execution: boolean;
  hitl_approvals_needed: number;
}

export interface SimulationResult {
  goal: string;
  summary?: SimulationSummary;
  policy_checks?: Array<{ tool: string; result: string }>;
  plan?: { steps: string[] };
}

export const simulationApi = {
  runGovernance: (goal: string) =>
    request<{ summary: SimulationSummary; policy_checks: Array<{ tool: string; result: string }> }>(
      "/governance/simulate",
      { method: "POST", body: JSON.stringify({ goal }) }
    ),
  runDryRun: (goal: string) =>
    request<{ steps?: string[]; plan?: { steps: string[] } }>(
      "/goals",
      { method: "POST", body: JSON.stringify({ goal, dry_run: true }) }
    ),
  /** Run via the real enterprise simulation engine (non-streaming) */
  run: (goal: string, mockTools: Record<string, string> = {}, agentId?: string) =>
    request<{
      run_id: string;
      status: string;
      steps: Array<{ step: number; tool?: string; output: string; mock_hit?: boolean; cost_usd?: number }>;
      cost_usd: number;
      iterations: number;
      used_real_llm: boolean;
      message?: string;
      result?: string;
    }>("/enterprise/simulation", {
      method: "POST",
      body: JSON.stringify({ goal, mock_tools: mockTools, agent_id: agentId }),
    }),
  /** Path for SSE streaming simulation — use with fetch() directly */
  streamPath: () => "/enterprise/simulation/stream",
  getRun: (runId: string) =>
    request<{ run_id: string; status: string; steps: unknown[]; cost_usd: number }>(
      `/enterprise/simulation/${runId}`
    ),
  getAvailableTools: () =>
    request<{ tools: Array<{ name: string; description: string; server_id: string }>; total: number }>(
      "/enterprise/simulation/available-tools"
    ),
};

// ── Enterprise (data residency + compliance export) ───────────────────────────

export interface DataResidencyInfo {
  region: string;
  data_center?: string;
  compliance_frameworks?: string[];
  description?: string;
}

export interface EnterpriseExportResult {
  download_url?: string;
  expires_at?: string;
  size_bytes?: number;
  message?: string;
}

export const enterpriseApi = {
  getResidency: () => request<DataResidencyInfo>("/enterprise/compliance/residency"),
  listRegions: () => request<DataResidencyInfo[]>("/enterprise/compliance/regions"),
  exportData: () => request<EnterpriseExportResult>("/enterprise/compliance/export"),
  purgeData: () =>
    request<{ message: string }>("/enterprise/compliance/delete", { method: "POST" }),
};

// ── Playground (agent simulation with mock tools) ─────────────────────────────

export interface PlaygroundStep {
  step: string;
  tool?: string;
  output?: string;
}

export interface PlaygroundResult {
  status: string;
  steps: PlaygroundStep[];
  cost_usd?: number;
  message?: string;
}

export const playgroundApi = {
  simulate: (goal: string, mockTools: Record<string, unknown>) =>
    request<PlaygroundResult>("/enterprise/simulation", {
      method: "POST",
      body: JSON.stringify({ goal, mock_tools: mockTools }),
    }),
};

// ── Insights API ──────────────────────────────────────────────────────────────

export interface CostEstimate {
  estimated_cost_usd: { min: number; mean: number; max: number };
  estimated_duration_s: { min: number; mean: number; max: number };
  estimated_iterations: { min: number; mean: number; max: number };
  success_probability: number;
  similar_goals_count: number;
  confidence: "low" | "medium" | "high";
  based_on: string;
}

export interface ExecutionGraph {
  goal_id: string;
  nodes: Array<{ id: string; type: string; label: string; data: Record<string, unknown> }>;
  edges: Array<{ id: string; source: string; target: string }>;
  stats: { total_nodes: number; total_edges: number; tool_calls: number; unique_tools: number };
}

export interface FailureAnalysis {
  goal_id: string;
  goal: string;
  status: string;
  failure_reason: string;
  suggestions: Array<{ action: string; description: string }>;
  iterations_used: number;
  cost_usd: number;
}

export interface AgentHealth {
  agent_id: string;
  health: {
    speed: number;
    accuracy: number;
    cost_efficiency: number;
    tool_coverage: number;
    success_rate: number;
    coherence: number;
  };
  sample_size: number;
}

export const insightsApi = {
  estimateGoal: (goal: string, agentId?: string) =>
    request<CostEstimate>("/insights/estimate", {
      method: "POST",
      body: JSON.stringify({ goal, agent_id: agentId }),
    }),
  getExecutionGraph: (goalId: string) =>
    request<ExecutionGraph>(`/insights/graph/${goalId}`),
  analyzeFailure: (goalId: string) =>
    request<FailureAnalysis>(`/insights/analysis/${goalId}`),
  queryGoals: (query: string, entity: "goals" | "agents" | "connectors" = "goals", limit = 20) =>
    request<{ results: GoalResponse[]; total: number; query_parsed: Record<string, unknown> }>(
      "/insights/query",
      { method: "POST", body: JSON.stringify({ query, entity, limit }) }
    ),
  getAgentHealth: (agentId: string) =>
    request<AgentHealth>(`/insights/agent-health/${agentId}`),
  getBenchmarks: () =>
    request<{
      platform_avg_success_rate: number;
      platform_avg_cost_usd: number;
      platform_avg_duration_s: number;
      top_10_pct_success_rate: number;
      percentile_bands: Record<string, Record<string, number>>;
    }>("/insights/benchmarks"),
};

// ── Goal Templates API ────────────────────────────────────────────────────────

export interface GoalTemplate {
  id: string;
  name: string;
  description: string;
  goal_text: string;
  domain: string;
  parameters: Array<{
    name: string;
    description: string;
    required: boolean;
    default?: string;
  }>;
  use_count: number;
  version: number;
  created_at: string;
}

export const templatesApi = {
  list: (domain?: string) =>
    request<GoalTemplate[]>(`/templates${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`),
  get: (id: string) => request<GoalTemplate>(`/templates/${id}`),
  create: (data: { name: string; description?: string; goal_text: string; domain?: string }) =>
    request<GoalTemplate>("/templates", { method: "POST", body: JSON.stringify(data) }),
  update: (id: string, data: { name: string; description?: string; goal_text: string; domain?: string }) =>
    request<void>(`/templates/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  delete: (id: string) => request<void>(`/templates/${id}`, { method: "DELETE" }),
  instantiate: (id: string, parameters: Record<string, string>, submit = false, agentId?: string) =>
    request<{ template_id: string; instantiated_goal: string; parameters_used: Record<string, string>; submitted_goal?: GoalResponse }>(
      `/templates/${id}/instantiate`,
      { method: "POST", body: JSON.stringify({ parameters, submit, agent_id: agentId }) }
    ),
};

// ── Marketplace V2 ───────────────────────────────────────────────────────────

export interface MarketplaceV2Template {
  template_id: string;
  slug: string;
  name: string;
  description: string;
  long_description?: string;
  domain: string;
  subdomain?: string;
  category?: string;
  tags?: string[];
  required_connectors: string[];
  optional_connectors?: string[];
  autonomy_mode: string;
  author_name?: string;
  icon_url?: string | null;
  visibility: string;
  review_status: string;
  is_builtin: boolean;
  is_verified: boolean;
  install_count: number;
  rating_avg?: number;
  rating_count?: number;
  version: string;
  template_config?: {
    goal_template?: string;
    autonomy_mode?: string;
    [key: string]: unknown;
  };
  parameters_schema?: {
    properties?: Record<string, {
      type?: string;
      description?: string;
      format?: string;
      enum?: string[];
      default?: string | number | boolean;
    }>;
    required?: string[];
  };
}

export interface MarketplaceReview {
  reviewer_tenant_id: string;
  rating: number;
  title?: string;
  body?: string;
  helpful_count: number;
  verified_install: boolean;
  created_at?: string;
}

export interface MarketplaceDeployResult {
  success: boolean;
  agent_id?: string;
  agent_name?: string;
  template_name?: string;
  install_id?: string;
  error?: string;
}

export const marketplaceApi = {
  /** V2 — paginated listing with optional search + domain filter */
  list: (params: { domain?: string; search?: string; page?: number; page_size?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.domain) q.set("domain", params.domain);
    if (params.search) q.set("search", params.search);
    if (params.page != null) q.set("page", String(params.page));
    if (params.page_size != null) q.set("page_size", String(params.page_size));
    const qs = q.toString();
    return request<{ templates: MarketplaceV2Template[]; total: number; page: number; page_size: number }>(
      `/marketplace/templates${qs ? `?${qs}` : ""}`
    );
  },
  get: (id: string) => request<MarketplaceV2Template>(`/marketplace/templates/${id}`),
  deploy: (id: string, params: Record<string, string> = {}, agentName?: string) =>
    request<MarketplaceDeployResult>(`/marketplace/templates/${id}/deploy`, {
      method: "POST",
      body: JSON.stringify({ parameters: params, agent_name: agentName }),
    }),
  getReviews: (id: string) => request<MarketplaceReview[]>(`/marketplace/templates/${id}/reviews`),
  addReview: (id: string, review: { rating: number; title?: string; body?: string }) =>
    request<MarketplaceReview>(`/marketplace/templates/${id}/reviews`, {
      method: "POST",
      body: JSON.stringify(review),
    }),
  search: (query: string, domain?: string, limit = 20) =>
    request<{ results: MarketplaceV2Template[]; total: number; query: string }>(
      "/marketplace/search",
      { method: "POST", body: JSON.stringify({ query, domain, limit }) }
    ),
  /** V1 publish — still used for community submissions */
  publish: (data: {
    name: string;
    domain: string;
    description: string;
    goal_template: string;
    autonomy_mode?: string;
    connectors?: string[];
  }) =>
    request<{ template_id: string; name: string }>("/marketplace/publish", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

// ── Agent Credentials (Spec 1) ────────────────────────────────────────────────

export interface AgentCredential {
  key_id: string;
  key_type: "jwt" | "api_key" | "mtls";
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  description?: string;
  status: "active" | "revoked";
}

export interface IssueCredentialRequest {
  key_type: "jwt" | "api_key" | "mtls";
  scopes: string[];
  expires_in_days?: number;
  description?: string;
}

export interface IssuedCredential extends AgentCredential {
  private_key?: string;
}

export const credentialsApi = {
  list: (agentId: string) => request<AgentCredential[]>(`/agents/${agentId}/credentials`),
  issue: (agentId: string, req: IssueCredentialRequest) =>
    request<IssuedCredential>(`/agents/${agentId}/credentials`, { method: "POST", body: JSON.stringify(req) }),
  revoke: (agentId: string, keyId: string) =>
    request<void>(`/agents/${agentId}/credentials/${keyId}`, { method: "DELETE" }),
  getToken: (agentId: string) =>
    request<{ token: string; expires_at: string }>(`/agents/${agentId}/token`, { method: "POST" }),
};

// ── Guardrails (Spec 3) ───────────────────────────────────────────────────────

export interface GuardrailConfig {
  id: string;
  name: string;
  rule_type: string;
  severity: "critical" | "high" | "medium" | "low";
  enabled: boolean;
  layers: string[];
  config: Record<string, unknown>;
  created_at: string;
}

export interface CreateGuardrailRequest {
  name: string;
  rule_type: string;
  severity: "critical" | "high" | "medium" | "low";
  layers: string[];
  config: Record<string, unknown>;
}

export interface GuardrailTestResult {
  passed: boolean;
  risk_score: number;
  violations: Array<{ type: string; message: string; severity: string }>;
}

export interface GuardrailViolation {
  id: string;
  guardrail_id: string;
  guardrail_name: string;
  type: string;
  severity: string;
  message: string;
  goal_id?: string;
  agent_id?: string;
  created_at: string;
}

export interface GuardrailStats {
  total_24h: number;
  total_all: number;
  by_severity: Record<string, number>;
  by_layer: Record<string, number>;
  top_categories: Array<{ category: string; count: number }>;
  risk_score_p95: number;
}

export const guardrailsApi = {
  list: () =>
    request<{ configs: GuardrailConfig[]; total: number } | GuardrailConfig[]>("/guardrails").then(
      (res) => (Array.isArray(res) ? res : (res as any).configs ?? [])
    ) as Promise<GuardrailConfig[]>,
  create: (body: CreateGuardrailRequest) =>
    request<GuardrailConfig>("/guardrails", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: Partial<CreateGuardrailRequest> & { enabled?: boolean }) =>
    request<void>(`/guardrails/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  delete: (id: string) => request<void>(`/guardrails/${id}`, { method: "DELETE" }),
  test: (body: { text: string; rule_id?: string; layer?: string }) =>
    request<GuardrailTestResult>("/guardrails/test", { method: "POST", body: JSON.stringify(body) }),
  getViolations: (params?: { limit?: number; severity?: string; goal_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.goal_id) qs.set("goal_id", params.goal_id);
    const q = qs.toString();
    return request<{ violations: GuardrailViolation[]; total: number } | GuardrailViolation[]>(
      `/guardrails/violations${q ? `?${q}` : ""}`
    ).then(
      (res) => (Array.isArray(res) ? res : (res as any).violations ?? [])
    ) as Promise<GuardrailViolation[]>;
  },
  getStats: () => request<GuardrailStats>("/guardrails/stats"),
};

// ── Costs (Spec 6) ───────────────────────────────────────────────────────────

export interface CostSummary {
  total_cost_usd: number;
  cost_by_day: Array<{ date: string; cost_usd: number }>;
  cost_by_model: Record<string, number>;
  daily_budget_usd: number;
  budget_utilization: number;
}

export interface AgentCost {
  agent_id: string;
  agent_name: string;
  total_cost_usd: number;
  goal_count: number;
  avg_cost_per_goal: number;
}

export interface CostPrediction {
  estimated_cost_usd: { min: number; mean: number; max: number };
  confidence: "low" | "medium" | "high";
}

export interface BudgetConfig {
  /** DB-backed: per_goal_usd / per_tenant_daily_usd / per_agent_daily_usd */
  per_goal_usd: number;
  per_tenant_daily_usd: number;
  per_agent_daily_usd?: Record<string, number>;
  alert_pct_thresholds: number[];
  // legacy aliases (used by older components)
  daily_budget_usd?: number;
  per_goal_budget_usd?: number;
  per_agent_budgets?: Record<string, number>;
  alert_threshold_pct?: number;
}

export interface CostAnomaly {
  // Actual backend shape from /costs/anomalies
  tenant_id?: string;
  agent_id?: string;
  anomaly_type: string;
  cost_actual_usd: number;
  cost_baseline_usd: number;
  sigma_deviation: number;
  detected_at: string;
  // Frontend-friendly aliases (computed on fetch)
  id?: string;
  type?: string;
  message?: string;
  cost_delta_usd?: number;
  severity?: "low" | "medium" | "high";
}

export const costsApi = {
  getSummary: () => request<CostSummary>("/costs/summary"),
  getPerAgent: () =>
    request<{ agents: AgentCost[]; period_days?: number } | AgentCost[]>("/costs/per-agent").then(
      (res) => (Array.isArray(res) ? res : (res as any).agents ?? [])
    ) as Promise<AgentCost[]>,
  predict: (goal: string) =>
    request<CostPrediction>("/costs/predict", { method: "POST", body: JSON.stringify({ goal }) }),
  getBudgets: () =>
    request<{ daily_limit?: number; per_goal_usd?: number; per_tenant_daily_usd?: number; budget_pct_remaining?: number; daily_spent?: number }>("/costs/budgets"),
  updateBudgets: (body: { per_goal_usd: number; per_tenant_daily_usd: number; per_agent_daily_usd?: Record<string, number>; alert_pct_thresholds?: number[] }) =>
    request<void>("/costs/budgets", { method: "PUT", body: JSON.stringify(body) }),
  getAnomalies: () =>
    request<{ anomalies: CostAnomaly[] } | CostAnomaly[]>("/costs/anomalies").then((res) => {
      const raw = Array.isArray(res) ? res : (res as { anomalies: CostAnomaly[] }).anomalies ?? [];
      return raw.map((a, i) => ({
        ...a,
        id: a.id ?? `anomaly-${i}`,
        type: a.type ?? a.anomaly_type ?? "unknown",
        message: a.message ?? `${a.anomaly_type ?? "Anomaly"}: $${a.cost_actual_usd?.toFixed(2)} vs baseline $${a.cost_baseline_usd?.toFixed(2)} (${a.sigma_deviation}σ)`,
        cost_delta_usd: a.cost_delta_usd ?? (a.cost_actual_usd - a.cost_baseline_usd),
        severity: a.severity ?? (a.sigma_deviation > 3 ? "high" : a.sigma_deviation > 2 ? "medium" : "low") as "low" | "medium" | "high",
      }));
    }) as Promise<CostAnomaly[]>,
};

// ── Self-Improvement (Spec 9) ─────────────────────────────────────────────────

export interface Experiment {
  id: string;
  name: string;
  agent_id: string;
  status: "running" | "concluded" | "pending";
  control_config: Record<string, unknown>;
  challenger_config: Record<string, unknown>;
  lift_pct: number | null;
  started_at: string;
  concluded_at: string | null;
}

export interface Suggestion {
  id: string;
  type: string;
  description: string;
  confidence: number;
  agent_id?: string;
  status: "pending" | "applied" | "rejected";
  created_at: string;
}

export const selfImprovementApi = {
  listExperiments: () => request<Experiment[]>("/intelligence/experiments"),
  getSuggestions: () => request<Suggestion[]>("/intelligence/suggestions"),
  applySuggestion: (id: string) =>
    request<void>(`/intelligence/suggestions/${id}/apply`, { method: "POST" }),
  rejectSuggestion: (id: string) =>
    request<void>(`/intelligence/suggestions/${id}/reject`, { method: "POST" }),
  rollbackExperiment: (id: string, reason: string) =>
    request<{ experiment_id: string; agent_id: string; status: string; reason: string }>(
      `/intelligence/experiments/${id}/rollback`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      }
    ),
};
