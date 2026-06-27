/**
 * Typed API client for the AgentVerse backend.
 * Base URL is injected from environment — VITE_API_URL defaults to http://localhost:8000.
 * In development the Vite proxy rewrites /api → localhost:8000, but direct URL
 * works too and is required for production builds.
 */

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

/** Exported alias for use in feature pages. */
export const API_BASE = API_BASE_URL;

function getApiKey(): string {
  return localStorage.getItem("av_api_key") ?? "";
}

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

export const goalsApi = {
  list: () => request<{ goals: GoalResponse[] }>("/goals"),
  submit: (body: GoalRequest) =>
    request<GoalResponse>("/goals", { method: "POST", body: JSON.stringify(body) }),
  get: (id: string) => request<GoalResponse>(`/goals/${id}`),
  cancel: (id: string) =>
    request<GoalResponse>(`/goals/${id}/cancel`, { method: "POST" }),
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

export const agentsApi = {
  list: () => request<AgentResponse[]>("/agents"),
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

export const connectorsApi = {
  catalog: () => request<CatalogEntry[]>("/connectors/catalog"),
  list: () => request<ConnectorResponse[]>("/connectors"),
  register: (body: ConnectorRequest) =>
    request<ConnectorResponse>("/connectors", { method: "POST", body: JSON.stringify(body) }),
  unregister: (id: string) => request<void>(`/connectors/${id}`, { method: "DELETE" }),
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
