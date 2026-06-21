/**
 * Typed API client for the AgentVerse backend.
 * Base URL is injected from environment — VITE_API_BASE_URL defaults to /api
 * (proxied to localhost:8000 in dev).
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

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

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
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
}

export interface GoalResponse {
  id: string;
  status: string;
  goal: string;
  steps?: StepResponse[];
  iterations?: number;
  cost_usd?: number;
}

export interface StepResponse {
  step_id: string;
  description: string;
  status: string;
  output: string;
}

export const goalsApi = {
  submit: (body: GoalRequest) =>
    request<GoalResponse>("/goals", { method: "POST", body: JSON.stringify(body) }),
  get: (id: string) => request<GoalResponse>(`/goals/${id}`),
  cancel: (id: string) =>
    request<GoalResponse>(`/goals/${id}/cancel`, { method: "POST" }),
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
