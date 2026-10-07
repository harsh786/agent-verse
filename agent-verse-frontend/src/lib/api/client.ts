/**
 * Typed API client for the AgentVerse backend.
 * Base URL is injected from environment — VITE_API_BASE_URL defaults to http://localhost:8000.
 * In development the Vite proxy rewrites /api → localhost:8000, but direct URL
 * works too and is required for production builds.
 */

import { getMfaHeader, useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import type { ResultArtifact } from '@/features/goals/resultArtifact';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

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

/** Extra, non-fetch options for {@link request}. Kept separate from RequestInit
 *  so callers opt in explicitly and existing 2-arg call sites are unaffected. */
export interface RequestMeta {
  /** Suppress the automatic "Server error" toast on a 5xx. Use for endpoints
   *  behind a feature flag (they return 503 by design) where the caller renders
   *  its own informational state instead. */
  silenceServerErrorToast?: boolean;
}

/**
 * Human-readable reason from an error body. Handles our `{error: {message}}`
 * envelope, FastAPI's string `detail`, and RFC-7807-style object details
 * (`{detail: {title, detail, ...}}`) — which previously became "[object Object]".
 */
export function errorMessageFromBody(body: unknown): string | undefined {
  if (!body || typeof body !== "object") return undefined;
  const b = body as { error?: { message?: unknown }; detail?: unknown };
  if (typeof b.error?.message === "string" && b.error.message) return b.error.message;
  const d = b.detail;
  if (typeof d === "string" && d) return d;
  if (d && typeof d === "object" && !Array.isArray(d)) {
    const o = d as { detail?: unknown; message?: unknown; title?: unknown };
    for (const v of [o.detail, o.message, o.title]) {
      if (typeof v === "string" && v) return v;
    }
  }
  if (Array.isArray(d)) {
    const msgs = d
      .map((e) => (e && typeof e === "object" ? (e as { msg?: unknown }).msg : e))
      .filter((m): m is string => typeof m === "string" && m.length > 0);
    if (msgs.length) return msgs.join("; ");
  }
  return undefined;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  meta: RequestMeta = {}
): Promise<T> {
  const { ssoMode, accessToken } = useAuthStore.getState();
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    // Don't set Content-Type for FormData — the browser must set it with the multipart
    // boundary. Forcing application/json here would lose the boundary and corrupt the upload.
    ...(!(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
    ...(options.headers as Record<string, string> | undefined),
  };
  // SSO mode: send Keycloak JWT as a Bearer token; the backend middleware
  // validates it and resolves the tenant without an API key.
  if (ssoMode && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  } else if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  // Second factor: the session token from /auth/mfa/verify (never sent before,
  // so every call after verification 401'd with MFA_REQUIRED).
  Object.assign(headers, getMfaHeader());

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch (networkErr) {
    toast({ kind: 'error', message: 'Network error — could not reach the server.' });
    throw networkErr;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: { message: res.statusText } }));
    // Prefer our envelope's error.message, then FastAPI's `detail`, then the raw status
    // text — so a toast/ApiError carries the real reason, not "Service Unavailable".
    const message = errorMessageFromBody(body) ?? res.statusText;
    if (res.status === 401) {
      const code = (body as { error?: { code?: unknown } } | undefined)?.error?.code;
      if (code === "MFA_REQUIRED" || code === "MFA_SESSION_EXPIRED") {
        // The credentials are fine; only the second factor is missing/expired.
        // Drop the stale token and re-prompt instead of logging the user out.
        const { setMfaToken, setMfaRequired } = useAuthStore.getState();
        setMfaToken(null);
        setMfaRequired(true);
        toast({ kind: 'error', message: 'Two-factor verification required.' });
        redirectToMfa();
        throw new ApiError(401, message, body);
      }
      const { logout } = useAuthStore.getState();
      logout();
      toast({ kind: 'error', message: 'Session expired — please sign in again.' });
      throw new ApiError(401, message, body);
    }
    if (res.status >= 500 && !meta.silenceServerErrorToast) {
      toast({ kind: 'error', message: `Server error: ${message}` });
    }
    throw new ApiError(res.status, message, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const MFA_VERIFY_PATH = "/auth/mfa";

function redirectToMfa(): void {
  try {
    if (typeof window !== "undefined" && window.location.pathname !== MFA_VERIFY_PATH) {
      window.location.assign(MFA_VERIFY_PATH);
    }
  } catch {
    // Non-browser / test environment: the mfaRequired flag is still set.
  }
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

/** Shown when the backend refused an LLM call because the tenant's budget is spent. */
export const LLM_BUDGET_EXHAUSTED_MESSAGE =
  'LLM budget exhausted — no model call was made. Raise the budget or try again later.';

/** True for the backend's 429 `{code: "llm_budget_exhausted"}` refusal. */
export function isLlmBudgetExhausted(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.status !== 429) return false;
  const body = e.body as { code?: unknown } | undefined;
  return body?.code === 'llm_budget_exhausted';
}

/** A clear message for a failed LLM-backed action (search, insights, OCR, skills…). */
export function llmErrorMessage(e: unknown, fallback: string): string {
  if (isLlmBudgetExhausted(e)) return LLM_BUDGET_EXHAUSTED_MESSAGE;
  if (e instanceof ApiError) return e.message || `${fallback} (${e.status})`;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

/** Public alias for use in feature-level API modules (e.g. civilizationApi). */
export const apiFetch = request;

/**
 * Fetches a backend-authored path (e.g. a `download_url` returned by an
 * export/report endpoint) as a Blob, attaching the same auth headers
 * `request()` uses (SSO Bearer token or API-key header).
 *
 * A bare `<a href={download_url}>` is wrong for two independent reasons: (1)
 * `download_url` is a backend-relative path, so the browser resolves it
 * against the frontend's own origin, not `API_BASE_URL`; (2) even with the
 * origin fixed, an anchor tag can't attach a custom Authorization/X-API-Key
 * header, so a request to an auth-gated download endpoint would 401. Use
 * this + `triggerBlobDownload` instead of rendering a raw anchor tag for any
 * authenticated file download.
 */
export async function downloadAuthenticated(path: string): Promise<Blob> {
  const { ssoMode, accessToken } = useAuthStore.getState();
  const apiKey = getApiKey();
  const headers: Record<string, string> = { ...getMfaHeader() };
  if (ssoMode && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  } else if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  const res = await fetch(`${API_BASE_URL}${path}`, { headers });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || `Download failed (${res.status})`, text);
  }
  return res.blob();
}

/** Saves a Blob to disk under `filename` via a transient object URL — the
 * standard way to trigger a browser "Save As" for programmatically-fetched
 * content (as opposed to a navigable URL an anchor tag could point at). */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Returns a cancelable version of request.
 * Call `.cancel()` to abort in-flight requests (e.g. on component unmount).
 *
 * @example
 * const { promise, cancel } = requestWithCancel<User[]>('/users');
 * useEffect(() => () => cancel(), []);
 */
export function requestWithCancel<T>(path: string, options: RequestInit = {}) {
  const controller = new AbortController();
  const promise = request<T>(path, { ...options, signal: controller.signal });
  return { promise, cancel: () => controller.abort() };
}

/**
 * Upload a file (or any FormData payload) to the given path.
 * Content-Type is intentionally NOT set so the browser supplies the multipart boundary.
 */
export function uploadFile<T>(path: string, formData: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: formData });
}

/**
 * Generic JSON request helper — exported for testing and ad-hoc use.
 * Sets Content-Type: application/json automatically (FormData excluded by the
 * request() function, but data passed here is always serialised as JSON).
 */
export function apiRequest<T>(method: string, path: string, data?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
  });
}

// ── Goals ────────────────────────────────────────────────────────────────────

export interface GoalRequest {
  goal: string;
  priority?: string;
  dry_run?: boolean;
  agent_id?: string;
  workflow_mode?: string;
  /** Multi-agent fan-out: one goal per agent (2-5). */
  agent_ids?: string[];
  /** Multimodal attachments (Gap 2) */
  attachments?: Array<{ type: string; url?: string; data?: string; name?: string }>;
  /** Single image shorthand (Gap 2) */
  image_url?: string;
  /** Override the tenant's default model for this goal (Gap 1) */
  model_override?: string;
  strategy_override?: string;
  auxiliary_strategies?: string[];
  pattern_limits?: Partial<{
    calls: number;
    nodes: number;
    edges: number;
    depth: number;
    fan_out: number;
    rounds: number;
    tokens: number;
    duration_seconds: number;
    cost_usd: number;
  }>;
}

// ── Ghost Run types ───────────────────────────────────────────────────────────

export interface GhostRunStrategy {
  name: string;
  workflow_mode: string;
  priority: string;
  agent_id?: string;
}

export interface GhostRunStrategyResult {
  name: string;
  goal_id: string | null;
  error: string | null;
  status: string;
}

export interface GhostRunResponse {
  ghost_run_id: string;
  goal_ids: Record<string, string>;
  strategies: GhostRunStrategyResult[];
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
  event_count?: number;
  result_artifact?: ResultArtifact;
  /** Agent that executed (or is executing) this goal */
  agent_id?: string | null;
  agent_name?: string | null;
  /** Workflow mode: single_agent | multi_agent | debate */
  workflow_mode?: string;
  /** Error message if the goal failed */
  error_message?: string;
  /** NF-14: sanitized reason a failed / cancelled goal ended (no secrets or hosts) */
  failure_reason?: string | null;
  /** NF-14: short code, e.g. approval_expired | runner_lost | timeout | error */
  terminal_reason?: string | null;
  /** Verifier feedback on last iteration */
  verification_feedback?: string;
  /** Priority: normal | high | low */
  priority?: string;
  /** Whether this was a dry run */
  dry_run?: boolean;
  /** The fan-out parent this goal was spawned by (null for a top-level goal) */
  parent_goal_id?: string | null;
  /** Sub-goals of a fan-out parent (real goals, max 64), with their live status */
  sub_goals?: SubGoalSummary[] | null;
}

/** One sub-goal of a fan-out parent, as listed on GET /goals/{id}. */
export interface SubGoalSummary {
  goal_id: string;
  status: string;
  goal: string;
  task_key?: string | null;
  kind?: string | null;
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

export interface EvalSuggestion {
  dimension: string;
  score: number;
  threshold: number;
  suggestion: string;
}

export interface EvalSuggestions {
  goal_id: string;
  status: "evaluated" | "not_evaluated";
  pass_threshold: number | null;
  suggestions: EvalSuggestion[];
  count: number;
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
  /** POST /goals/:id/eval only: how accuracy/coherence were judged. */
  scorer?: "llm" | "partial" | "heuristic";
  /** POST /goals/:id/eval only: the scorecard was saved for every replica. */
  persisted?: boolean;
}

// ── Pattern selection types ────────────────────────────────────────────────────

export interface PatternRationale {
  pattern: string;
  name: string;
  category: 'reasoning' | 'multi_agent' | 'safety' | string;
  why: string;
}

export interface AgentPatternCatalogEntry {
  id: string;
  name: string;
  description: string;
  state: string;
  available: boolean;
  cost_class: string;
  latency_class: string;
}

export interface PatternSelectionResponse {
  goal_id: string;
  status?: string;
  source: 'auto' | 'override' | string;
  override?: string | null;
  primary_pattern: string;
  primary_pattern_name: string;
  reasoning_patterns: string[];
  multi_agent_patterns: string[];
  safety_patterns: string[];
  autonomy_mode: string;
  max_iterations: number;
  advanced_tier_enabled: boolean;
  advanced_tier_gated: boolean;
  goal_properties: {
    complexity: string;
    domain: string;
    risk: string;
    multi_step: boolean;
    requires_code: boolean;
    classifier_confidence: number;
  };
  rationale: PatternRationale[];
  available_patterns: AgentPatternCatalogEntry[];
  /** The selection above is the selector's recommendation from the goal text. */
  selection_kind?: 'recommendation' | string;
  /** What the goal's runtime actually ran (its strategy_execution record). */
  execution?:
    | { state: 'pending' }
    | {
        state: 'recorded';
        driver: string;
        patterns: string[];
        requested_primary?: string | null;
        downgrades: Array<Record<string, string>>;
      };
  executed_patterns?: string[];
  /** null while the runtime has not been built yet. */
  matches_execution?: boolean | null;
  strategy_downgraded?: boolean;
  strategy_downgrade?: { reason?: string } | null;
}

export const goalsApi = {
  // TODO(scale): the current backend GET /goals returns ALL goals for the tenant
  // and ignores status/search/page/page_size (verified against the route +
  // OpenAPI). These params are forwarded so the client is ready the moment the
  // backend adds pagination; until then GoalsListPage still filters/paginates
  // client-side. Needs a backend page/limit/cursor param.
  list: (params?: { status?: string; search?: string; page?: number; page_size?: number }) => {
    const q = new URLSearchParams();
    if (params?.status && params.status !== 'all') q.set('status', params.status);
    if (params?.search) q.set('search', params.search);
    if (params?.page) q.set('page', String(params.page));
    if (params?.page_size) q.set('page_size', String(params.page_size));
    const qs = q.toString();
    return request<{ goals: GoalResponse[] }>(`/goals${qs ? '?' + qs : ''}`);
  },
  submit: (body: GoalRequest) =>
    request<GoalResponse>("/goals", { method: "POST", body: JSON.stringify(body) }),
  get: (id: string) => request<GoalResponse>(`/goals/${id}`),
  /** The agent pattern this goal was routed to (auto-selected or overridden) + why. */
  getPatternSelection: (id: string) =>
    request<PatternSelectionResponse>(`/goals/${id}/pattern-selection`),
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
  /** Auto-suggested improvement actions derived from the goal's real eval scores
   *  (each dimension below the config-driven pass threshold, worst first). */
  getEvalSuggestions: (id: string) =>
    request<EvalSuggestions>(`/goals/${id}/eval/suggestions`),
  ghostRun: (body: { goal: string; strategies: GhostRunStrategy[] }) =>
    request<GhostRunResponse>("/goals/ghost-run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
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
  /** Reasoning-pattern opt-ins (enable_cot, enable_debate, ...), also flattened. */
  pattern_flags?: Record<string, boolean>;
  /** D3: listed in the public A2A directory (when the tenant's directory is on). */
  a2a_public?: boolean;
  /** Public card text shown in the A2A directory (never the prompt or tools). */
  a2a_description?: string;
  a2a_skills?: string[];
  eval_suite_id?: string | null;
  /**
   * a05-F095-04 decision: a config change to a fully-autonomous agent demotes
   * it and re-runs its eval suite; this is that re-validation (null = never).
   */
  autonomy_revalidation?: AutonomyRevalidation | null;
  /** True while demoted and waiting for the eval suite run to pass. */
  pending_promotion?: boolean;
  /** Clone of a fully-autonomous agent: why the clone was created bounded-autonomous. */
  autonomy_note?: string;
}

/** The re-validation of a demoted fully-autonomous agent (agents.autonomy_revalidation). */
export interface AutonomyRevalidation {
  state: 'pending' | 'promoted' | 'failed' | 'cancelled';
  reason: string;
  source?: string;
  from_mode?: string;
  eval_suite_id?: string | null;
  run_id?: string | null;
  demoted_at?: string;
  resolved_at?: string;
  pass_rate?: number | null;
  min_pass_rate_required?: number | null;
  error?: string;
  cancelled_reason?: string;
}

/** D3: the per-agent public A2A directory opt-in and its card text. */
export interface AgentA2AUpdate {
  a2a_public?: boolean;
  a2a_description?: string;
  a2a_skills?: string[];
}

// ── Agent extended types ──────────────────────────────────────────────────────

export interface CreateAgentRequest {
  name: string;
  autonomy_mode: string;
  goal_template?: string;
  description?: string;
  tools?: string[];
  model?: string;
  /** MCP connector server ids the agent may call as tools. */
  connector_ids?: string[];
  enable_cot?: boolean;
  enable_reflection?: boolean;
  enable_goal_tree?: boolean;
  enable_self_refine?: boolean;
  enable_self_consistency?: boolean;
  enable_tree_of_thoughts?: boolean;
  enable_peer_review?: boolean;
  enable_supervisor?: boolean;
  enable_debate?: boolean;
}

export interface AgentSnapshot {
  snapshot_id: string;
  agent_id: string;
  created_at: string;
  config: Record<string, unknown>;
}

/** The heuristic draft POST /agents/create hands back (502) when its LLM failed. */
export interface MetaAgentDraftConfig {
  name: string;
  goal_template: string;
  connectors: string[];
  trigger_type: string;
  autonomy_mode: string;
}

/** POST /agents/create success body. `agent_id` is accepted for older payloads. */
export interface MetaAgentCreateResponse {
  agent?: AgentResponse;
  agent_id?: string;
  meta_agent_config?: Record<string, unknown> & {
    generated_by?: string;
    /** Free-text governance ideas from the designer LLM — never applied. */
    policy_suggestions?: string[];
    policy_suggestions_applied?: boolean;
    policy_suggestions_note?: string;
  };
}

/** The id of the agent POST /agents/create made, whichever shape the body used. */
export function createdAgentId(r: MetaAgentCreateResponse | null | undefined): string {
  return r?.agent?.agent_id ?? r?.agent_id ?? "";
}

/**
 * A meta-agent create the backend refused because the designer LLM failed.
 * No agent exists yet; the user must explicitly confirm to create the draft.
 */
export interface HeuristicAgentDraft {
  detail: string;
  fallbackReason: string;
  draft: MetaAgentDraftConfig;
}

/**
 * Recognise the 502 `{detail, generated_by: "heuristic", draft_config}` answer
 * from POST /agents/create. Returns null for any other error.
 */
export function heuristicDraftFromError(err: unknown): HeuristicAgentDraft | null {
  if (!(err instanceof ApiError) || err.status !== 502) return null;
  const body = err.body as
    | { detail?: unknown; generated_by?: unknown; fallback_reason?: unknown; draft_config?: unknown }
    | undefined;
  if (!body || body.generated_by !== "heuristic") return null;
  const d = (body.draft_config ?? {}) as Partial<Record<keyof MetaAgentDraftConfig, unknown>>;
  return {
    detail: typeof body.detail === "string" ? body.detail : "The agent designer LLM did not return a usable config.",
    fallbackReason: typeof body.fallback_reason === "string" ? body.fallback_reason : "",
    draft: {
      name: typeof d.name === "string" ? d.name : "",
      goal_template: typeof d.goal_template === "string" ? d.goal_template : "",
      connectors: Array.isArray(d.connectors) ? d.connectors.map(String) : [],
      trigger_type: typeof d.trigger_type === "string" ? d.trigger_type : "",
      autonomy_mode: typeof d.autonomy_mode === "string" ? d.autonomy_mode : "",
    },
  };
}

export const agentsApi = {
  // TODO(scale): GET /agents returns the full list with no server-side
  // pagination/filter params (verified against the backend route + OpenAPI).
  // Callers must paginate/filter/sort client-side. Needs a backend
  // page/limit/cursor param before this can be pushed server-side.
  list: () => request<AgentResponse[]>("/agents"),
  get: (id: string) => request<AgentResponse>(`/agents/${id}`),
  create: (data: CreateAgentRequest) =>
    request<AgentResponse>("/agents", { method: "POST", body: JSON.stringify(data) }),
  /**
   * Meta-agent NL create. When the designer LLM fails the backend answers 502
   * with a heuristic draft and creates nothing (see {@link heuristicDraftFromError});
   * resend with `acceptHeuristic: true` only after the user confirms the draft.
   * The generic 5xx toast is suppressed — every caller renders this error inline.
   */
  createNl: (command: string, autorun = false, opts: { acceptHeuristic?: boolean } = {}) =>
    request<MetaAgentCreateResponse>(
      "/agents/create",
      {
        method: "POST",
        body: JSON.stringify({
          command,
          autorun,
          ...(opts.acceptHeuristic ? { accept_heuristic: true } : {}),
        }),
      },
      { silenceServerErrorToast: true },
    ),
  /** D3: opt the agent in/out of the public A2A directory and set its card text. */
  updateA2A: (id: string, data: AgentA2AUpdate) =>
    request<AgentResponse>(`/agents/${id}`, { method: "PUT", body: JSON.stringify(data) }),
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
    request<AgentResponse>(`/agents/${id}/clone`, { method: "POST", body: "{}" }),
  assignKnowledge: (agentId: string, knowledgeId: string) =>
    request<void>(`/agents/${agentId}/knowledge/${knowledgeId}`, { method: "POST" }),
  removeKnowledge: (agentId: string, knowledgeId: string) =>
    request<void>(`/agents/${agentId}/knowledge/${knowledgeId}`, { method: "DELETE" }),
  getRolloutGate: (id: string) =>
    request<{ gate_status: string; traffic_pct: number; conditions: string[] }>(
      `/agents/${id}/rollout-gate`
    ),
  checkReadiness: (id: string) =>
    request<{ ready: boolean; score?: number; issues?: string[]; checks?: Array<{ status: string; message: string }> }>(
      `/agents/${id}/readiness`
    ),
};

// ── Connectors ────────────────────────────────────────────────────────────────

export interface ConnectorRequest {
  /** This connection's own name (unique per tenant; the backend answers 409 on a duplicate). */
  name: string;
  /**
   * Built-in type of a NEW connection ("mongodb" / "builtin-mongodb"); several
   * named connections may share one type. 422 when unknown. Not changeable on update.
   */
  type?: string;
  url: string;
  auth_type: string;
  auth_config: Record<string, string>;
  description?: string;
  // When true, this connector's high-risk tools run without human approval in
  // autonomous goals (explicit per-connector opt-in; default-secure off).
  auto_approve?: boolean;
}

export interface ConnectorResponse {
  /** Opaque, unique per registered connector (instance) — never parse it. */
  server_id: string;
  /** The connection's own name (a tenant may have several of one type, e.g. two MongoDBs). */
  name: string;
  /** Unique-per-tenant display name of this connection (same as `name` on current backends). */
  display_name?: string;
  /** Canonical built-in type id this connection dispatches to ("builtin-mongodb"), null for remote MCP. */
  builtin_type?: string | null;
  /** Human name of the built-in type ("MongoDB"). */
  builtin_type_name?: string;
  url: string;
  // Real upstream API endpoint for a built-in connector (whose `url` is the
  // internal "builtin://" dispatch marker). Empty for local/unknown built-ins.
  upstream_url?: string;
  /** Masked connection URI for display (no userinfo; secret query values '<redacted>'). */
  display_url?: string;
  /** Catalog type key of a built-in connection ("mongodb"). */
  connector_type?: string;
  status?: string;
  auth_type?: string;
  /** Secrets and URI keys come back as '<redacted>'; values may be non-strings (tls: true). */
  auth_config?: Record<string, unknown>;
  last_tested?: string;
  test_result?: { success: boolean; latency_ms?: number; error?: string };
  has_builtin?: boolean;
  builtin_server_id?: string;
  auto_approve?: boolean;
}

export interface CatalogAuthField {
  key: string;
  label: string;
  placeholder: string;
  /** Renderer types the UI knows ('text' | 'password' | 'url' | 'email' |
   *  'textarea' | 'checkbox' | 'file' | 'select'); any other value a newer
   *  backend sends renders as text. */
  field_type: string;
  required: boolean;
  hint: string;
  /** Choices for a `select` field (optional; not every backend sends it). */
  options?: Array<string | { value: string; label: string }>;
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
  /** null when nothing was contacted (status "not_tested"). */
  reachable: boolean | null;
  latency_ms?: number;
  error?: string;
  /** Human-readable success detail, e.g. "Authenticated as @username (Full Name) · scopes: repo,read:org" */
  detail?: string;
  /** MCP endpoint URL confirmed to work */
  mcp_url?: string;
  http_status?: number;
  status?: string;
}

/** GET /connectors/oauth/start?server_id=… */
export interface PkceOAuthStart {
  server_id: string;
  auth_url: string;
  state: string;
  redirect_uri?: string;
  instructions?: string;
}

/** GET /connectors/oauth/callback — only `status: "connected"` means tokens were stored. */
export interface PkceOAuthCallback {
  server_id: string;
  status: "connected" | "pending_config" | "error" | string;
  message?: string;
  token_type?: string;
  scope?: string;
  has_refresh_token?: boolean;
}

// Server ids are opaque (e.g. `builtin-mongodb:<slug>`), so every path segment is encoded.
export const connectorsApi = {
  getCatalog: () => request<CatalogEntry[]>("/connectors/catalog"),
  list: () => request<ConnectorResponse[]>("/connectors"),
  get: (id: string) => request<ConnectorResponse>(`/connectors/${encodeURIComponent(id)}`),
  tools: (id: string) => request<{ name?: string; description?: string }[]>(`/connectors/${encodeURIComponent(id)}/tools`),
  register: (body: ConnectorRequest) =>
    request<ConnectorResponse>("/connectors", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: Partial<ConnectorRequest>) =>
    request<ConnectorResponse>(`/connectors/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  unregister: (id: string) => request<void>(`/connectors/${encodeURIComponent(id)}`, { method: "DELETE" }),
  test: (id: string) =>
    request<ConnectorTestResult>(`/connectors/${encodeURIComponent(id)}/test`, { method: "POST" }),
  /**
   * Start the real OAuth (PKCE) flow for a REGISTERED connector whose auth_type is
   * pkce / oauth_ac / oauth_cc. `auth_url` is only a URL when the connector's
   * auth_config has authorize_url + client_id; otherwise it is an instruction string.
   */
  startPkceOAuth: (serverId: string) =>
    request<PkceOAuthStart>(
      `/connectors/oauth/start?server_id=${encodeURIComponent(serverId)}`,
    ),
  /** Exchange the provider's code (PKCE verifier held server-side) and store real tokens. */
  completePkceOAuth: (serverId: string, code: string, state: string) =>
    request<PkceOAuthCallback>(
      `/connectors/oauth/callback?${new URLSearchParams({ server_id: serverId, code, state }).toString()}`,
    ),
  getUsage: (connectorId: string) =>
    request<{ goals: GoalResponse[]; total: number; success_rate: number | null; filtered: boolean }>(
      `/connectors/${encodeURIComponent(connectorId)}/usage`
    ),
};

// ── Model registry (generic, cost-aware model selection) ───────────────────────

export type ModelCapability = 'text_generation' | 'embedding' | 'vision' | 'ocr' | 'rerank';

export interface ConfiguredModel {
  /** "provider/model_id" — the identity used by preference orders. */
  key: string;
  provider: string;
  model_id: string;
  display_name: string;
  capabilities: string[];
  cost_per_1k_input: number;
  cost_per_1k_output: number;
  supports_tools: boolean;
  supports_vision: boolean;
  supports_structured_output: boolean;
  quality_score: number;
  is_available: boolean;
  /**
   * False when selection skips the model: its provider has no API key here and
   * the entry has neither its own endpoint URL nor its own saved key.
   */
  provider_ready: boolean;
  /**
   * Whether anything in this deployment can actually serve the model now. An
   * env-named model (DEFAULT_MODEL, EMBEDDING_MODEL, …) with no key or endpoint
   * behind it is false. Absent on older backends (treat as provider_ready).
   */
  servable?: boolean;
  /** Refused for this deployment (e.g. embedding dimension mismatch); never the primary. */
  refused?: boolean;
  refusal_reason?: string | null;
  source: 'env' | 'override';
  /** 1-based effective execution order within the capability. */
  rank: number;
  /**
   * Base URL of the OpenAI-compatible server that serves this model (vLLM,
   * Ollama `/v1`, on-prem). null = the provider's configured API.
   */
  base_url: string | null;
  /**
   * Whether the entry carries its own (vault-encrypted) endpoint credential.
   * The key itself is never returned; false = the provider's server-side key.
   */
  has_api_key?: boolean;
  /**
   * Embedding models: the vector width requested from the model (sent as
   * `dimensions`); null = the model's native width.
   */
  output_dimensions?: number | null;
  /** Embedding models: the known vector width (measured, requested or catalog). */
  dimensions?: number | null;
  /** Embedding models: the vector index width (EMBEDDING_DIM). */
  index_dimension?: number | null;
  dimension_mismatch?: boolean;
  dimension_reason?: string;
  /** Embedding models: a knowledge collection of this width can be bound to it. */
  collection_compatible?: boolean;
  collection_chunk_table?: string | null;
  collection_reason?: string;
  /**
   * Thinking-model control: "auto" (default — an empty, reasoning-only reply is
   * retried once with thinking off), "off" (always answer without reasoning),
   * "on" (keep reasoning).
   */
  thinking?: ThinkingMode;
  /** Reasoning tokens added to the budget when thinking is "on"; null = none. */
  thinking_budget_tokens?: number | null;
}

export type ThinkingMode = 'auto' | 'off' | 'on';

export interface CapabilityGroup {
  capability: ModelCapability | string;
  /**
   * "ready" when at least one model can serve the capability now; "not_ready"
   * when models are listed but none can. Absent on older backends.
   */
  status?: 'ready' | 'not_ready';
  /** How many listed models can serve the capability now. */
  ready_count?: number;
  selected_model_id: string;
  fallback_model_ids: string[];
  order_mode: 'preference' | 'cost';
  preference: string[];
  note?: string;
  /** Already sorted in effective execution order (preference first, then cheapest). */
  models: ConfiguredModel[];
}

/** GET /models/configured/access — can the caller modify the global registry? */
export interface ModelRegistryAccess {
  can_modify: boolean;
  via: 'admin_key' | 'tenant_admin' | null;
  needs_admin_key: boolean;
  reason: string;
}

export interface CatalogModel {
  model_id: string;
  display_name: string;
  capabilities: string[];
  cost_per_1k_input: number;
  cost_per_1k_output: number;
  supports_tools: boolean;
  supports_vision: boolean;
  quality_score: number;
  already_configured: boolean;
  /** Set when the catalog entry is served by a specific OpenAI-compatible endpoint. */
  base_url?: string | null;
}

export interface CatalogProvider {
  provider: string;
  label: string;
  /** True when the provider's API key is configured on the backend. */
  ready: boolean;
  /** Env var that enables the provider, e.g. "GROQ_API_KEY". */
  env_hint: string;
  models: CatalogModel[];
}

// The registry is deployment-global. Mutations are authorized either by the
// platform admin key (sent as X-Admin-Key, typed by the operator and kept in
// memory only) or by the signed-in tenant user holding the admin role on an
// operator tenant. Every registry call forwards the key when one was typed.
const _adminHeaders = (adminKey?: string): Record<string, string> =>
  adminKey ? { "X-Admin-Key": adminKey } : {};

/** POST /models/configured/test-endpoint — probe an OpenAI-compatible endpoint. */
export interface ModelEndpointTestRequest {
  provider: string;
  model_id: string;
  base_url: string;
  capabilities: string[];
  /** Used for this call only (never stored); omitted = the saved / provider key. */
  api_key?: string;
  /** Embedding probes: the width to request (`dimensions`); null = native width. */
  output_dimensions?: number | null;
  thinking?: ThinkingMode;
  thinking_budget_tokens?: number | null;
}

/** What a chat probe learned about a thinking (reasoning) model. */
export interface ThinkingProbe {
  mode: ThinkingMode;
  /** Reasoning tokens / reasoning text were observed on the plain call. */
  thinking_model: boolean;
  reasoning_tokens: number;
  /** null = not tried; false = the endpoint has no (or refused the) switch. */
  disable_supported: boolean | null;
  /** The model answered with thinking off (no reasoning tokens). */
  disabled_works: boolean | null;
  recommendation: string | null;
}

/**
 * Why a probe failed, classified by the backend: the key was rejected, the
 * endpoint does not serve the model, it cannot be reached, it does not support
 * the capability, the URL is refused, a thinking model ran out of budget, or a
 * 2xx answer lacked the expected payload.
 */
export type ProbeErrorKind =
  | 'auth'
  | 'model_not_served'
  | 'unreachable'
  | 'unsupported'
  | 'refused'
  | 'thinking_budget'
  | 'invalid_response'
  | 'http_error';

export type ProbeKind = 'chat' | 'vision' | 'embedding' | 'rerank';

/** One capability probe of Test connection (one real call). */
export interface ModelProbeCheck {
  probe: ProbeKind;
  /** The registry capabilities this probe covers (e.g. ["ocr", "vision"]). */
  capabilities: string[];
  ok: boolean;
  latency_ms: number;
  detail: string;
  error: string | null;
  error_kind: ProbeErrorKind | null;
  /** chat */
  thinking?: ThinkingProbe;
  /** vision: the model's answer about the probe image, and whether it read the word. */
  reply?: string;
  expected_text?: string;
  text_matched?: boolean | null;
  /** rerank: scores, best first; whether the relevant document ranked first. */
  scores?: { index: number; score: number; document: string }[];
  relevant_first?: boolean;
  /** embedding */
  dimensions?: number | null;
  index_dimension?: number | null;
  dimension_mismatch?: boolean;
  requested_dimensions?: number | null;
  dimensions_ignored?: boolean;
}

/** `ok: false` still arrives as HTTP 200 with `error` set; a refused URL is a 400. */
export interface ModelEndpointTestResult {
  ok: boolean;
  latency_ms: number;
  /** The primary probe the top-level fields describe. */
  probe: ProbeKind;
  /** Absent on older backends. */
  error_kind?: ProbeErrorKind | null;
  /** One entry per capability probe (absent on older backends). */
  checks?: ModelProbeCheck[];
  /** null when the server has no model listing to check against. */
  model_listed: boolean | null;
  served_models: string[];
  detail: string;
  error: string | null;
  /** Chat probes only. */
  thinking?: ThinkingProbe;
  /** Embedding probes: the measured vector width. */
  dimensions?: number | null;
  /** Embedding probes: the vector index width (EMBEDDING_DIM). */
  index_dimension?: number | null;
  dimension_mismatch?: boolean;
  dimension_reason?: string;
  /** Embedding probes: the width asked for (`dimensions`), null = native. */
  requested_dimensions?: number | null;
  /** The endpoint answered another width than the one asked for. */
  dimensions_ignored?: boolean;
}

/** GET /models/resolution — where a capability's / role's model comes from. */
export type ResolutionSource =
  | 'tenant_pin'
  | 'registry_order'
  | 'registry_cheapest'
  | 'deployment_profile'
  | 'env_pin'
  | 'local_default'
  | 'default'
  | 'none';

export interface ResolvedModelRef {
  model_id: string;
  provider: string | null;
  /** false = nothing in this deployment serves it; null = not a registry model. */
  servable: boolean | null;
}

export interface CapabilityResolution {
  capability: 'reasoning' | 'embedding' | 'vision' | 'ocr' | 'rerank' | string;
  label: string;
  /** false = this capability's call sites do not follow the registry yet. */
  routed: boolean;
  model: ResolvedModelRef | null;
  source: ResolutionSource | string;
  source_label: string;
  fallbacks: ResolvedModelRef[];
  warning: string | null;
  note: string | null;
}

export interface RoleResolution {
  task_type: string;
  label: string;
  /** Every runtime role label decided by this task type (planner, answer_synthesis, …). */
  roles: string[];
  routed_by_goal_router: boolean;
  model: ResolvedModelRef | null;
  source: ResolutionSource | string;
  source_label: string;
  fallbacks: ResolvedModelRef[];
  warning: string | null;
}

export interface ModelResolution {
  capabilities: CapabilityResolution[];
  roles: RoleResolution[];
  warnings: string[];
  sources?: Record<string, string>;
}

/** GET /models/plan-cap — would this model be clamped to the caller's plan tier? */
export interface ModelPlanCap {
  model_id: string;
  model_tier: 'low' | 'medium' | 'high';
  plan: string;
  plan_cap: 'low' | 'medium' | 'high' | null;
  clamped: boolean;
}

export const modelsApi = {
  planCap: (modelId: string) =>
    request<ModelPlanCap>(`/models/plan-cap?model_id=${encodeURIComponent(modelId)}`),
  access: (adminKey?: string) =>
    request<ModelRegistryAccess>("/models/configured/access", {
      headers: _adminHeaders(adminKey),
    }),
  /** What every capability and agent role resolves to now (read-only). */
  resolution: () => request<ModelResolution>("/models/resolution"),
  listConfigured: (adminKey?: string) =>
    request<{ capabilities: CapabilityGroup[]; total: number }>("/models/configured", {
      headers: _adminHeaders(adminKey),
    }),
  upsertConfigured: (
    body: Partial<Omit<ConfiguredModel, 'key' | 'rank' | 'source' | 'provider_ready'>> & {
      model_id: string;
      capabilities: string[];
      /** Write-only endpoint credential (vault-encrypted server-side); omitted = keep. */
      api_key?: string;
      /** Remove the saved endpoint credential. */
      clear_api_key?: boolean;
    },
    adminKey?: string
  ) =>
    request<{ status: string; model_id: string }>("/models/configured", {
      method: "POST",
      body: JSON.stringify(body),
      headers: _adminHeaders(adminKey),
    }),
  deleteConfigured: (provider: string, modelId: string, adminKey?: string) =>
    request<{ status: string; removed: boolean }>(
      `/models/configured/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}`,
      { method: "DELETE", headers: _adminHeaders(adminKey) }
    ),
  testEndpoint: (body: ModelEndpointTestRequest, adminKey?: string) =>
    request<ModelEndpointTestResult>("/models/configured/test-endpoint", {
      method: "POST",
      body: JSON.stringify(body),
      headers: _adminHeaders(adminKey),
    }),
  reseed: (adminKey?: string) =>
    request<{ status: string; configured_models: number }>("/models/configured/reseed", {
      method: "POST",
      headers: _adminHeaders(adminKey),
    }),
  catalog: (adminKey?: string) =>
    request<{ providers: CatalogProvider[] }>("/models/catalog", {
      headers: _adminHeaders(adminKey),
    }),
  /** Omit both fields to import the whole catalog. */
  importCatalog: (body: { providers?: string[]; model_ids?: string[] }, adminKey?: string) =>
    request<{ status: string; imported: number; skipped: number }>("/models/catalog/import", {
      method: "POST",
      body: JSON.stringify(body),
      headers: _adminHeaders(adminKey),
    }),
  /** Save the execution order for a capability (keys are "provider/model_id"). */
  savePreference: (capability: string, order: string[], adminKey?: string) =>
    request<{ status: string; capability: string; order: string[] }>(
      `/models/preferences/${encodeURIComponent(capability)}`,
      { method: "PUT", body: JSON.stringify({ order }), headers: _adminHeaders(adminKey) }
    ),
  /** Drop the saved order so the capability goes back to cheapest-first. */
  resetPreference: (capability: string, adminKey?: string) =>
    request<{ status: string; capability: string }>(
      `/models/preferences/${encodeURIComponent(capability)}`,
      { method: "DELETE", headers: _adminHeaders(adminKey) }
    ),
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
  /** admin | operator | approver | viewer (backend default: operator). */
  roles?: string[];
  created_at: string;
  last_used_at?: string;
  expires_at?: string | null;
}

/** What removing indexed chat transcripts did (owner decision 7). */
export interface ChatTranscriptRemoval {
  removed_documents?: number;
  /** Kept: under an active legal hold. */
  held_documents?: number;
  held_document_ids?: string[];
  /** More remain; a background task continues the removal. */
  pending?: boolean;
  continuation?: string;
}

export interface ChatTranscriptsKnowledgeSetting extends ChatTranscriptRemoval {
  enabled: boolean;
}

export interface ChatKnowledgeOptInState extends ChatTranscriptRemoval {
  /** The workspace switch (an admin's); opting in needs it on. */
  tenant_enabled?: boolean;
  opted_in: boolean;
  opted_in_at: string | null;
  revoked_at: string | null;
}

/** Owner decision 7: a person's own opt-in to index their chats as knowledge. */
export const chatKnowledgeApi = {
  getOptIn: () => request<ChatKnowledgeOptInState>("/chat/settings/knowledge"),
  setOptIn: (optedIn: boolean) =>
    request<ChatKnowledgeOptInState>("/chat/settings/knowledge", {
      method: "PUT",
      body: JSON.stringify({ opted_in: optedIn }),
    }),
};

/** a02-F036-02: the tenant-owned SMTP sender (the secret is never returned). */
export type SmtpTlsMode = "starttls" | "tls" | "none";

export interface TenantSmtpView {
  host: string;
  port: number;
  tls_mode: SmtpTlsMode;
  username: string;
  from_address: string;
  secret_set: boolean;
  secret_masked: string | null;
}

export interface TenantEmailSettings {
  tenant_id: string;
  recipient_allowlist: string[];
  smtp: TenantSmtpView | null;
  /** Which relay the agent email tool uses: the tenant's SMTP or the platform's. */
  relay: "tenant" | "platform";
  updated_at: string | null;
}

export interface TenantSmtpConfigInput {
  host: string;
  port: number;
  tls_mode: SmtpTlsMode;
  username: string;
  from_address: string;
  /** Password or API key. Omit to keep the stored one (same host, port and username). */
  secret?: string;
}

export interface SmtpTestResult {
  ok: boolean;
  stage: "policy" | "connect" | "tls" | "auth" | "send" | "done";
  message: string;
  smtp_code?: number;
  tested: "saved" | "candidate";
}

export const tenantsApi = {
  /** Owner decision 7: whether chat transcripts may become knowledge (off by default). */
  getChatTranscriptsKnowledge: () =>
    request<ChatTranscriptsKnowledgeSetting>("/tenants/me/chat-transcripts-knowledge"),
  /** Owner decision 7: enable / disable the chat transcript knowledge kind (admin only). */
  setChatTranscriptsKnowledge: (enabled: boolean) =>
    request<ChatTranscriptsKnowledgeSetting>("/tenants/me/chat-transcripts-knowledge", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  /** a02-F036-02: recipient allowlist + tenant SMTP sender (admin only). */
  getEmailSettings: () => request<TenantEmailSettings>("/tenants/me/email"),
  setEmailAllowlist: (entries: string[]) =>
    request<TenantEmailSettings>("/tenants/me/email/allowlist", {
      method: "PUT",
      body: JSON.stringify({ entries }),
    }),
  setEmailSmtp: (config: TenantSmtpConfigInput) =>
    request<TenantEmailSettings>("/tenants/me/email/smtp", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  deleteEmailSmtp: () =>
    request<TenantEmailSettings>("/tenants/me/email/smtp", { method: "DELETE" }),
  testEmailSmtp: (body: { config?: TenantSmtpConfigInput; send_to?: string } = {}) =>
    request<SmtpTestResult>("/tenants/me/email/smtp/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** D3: whether this tenant's opted-in agents are listed at /.well-known/agents. */
  getA2ADirectory: () => request<{ enabled: boolean }>("/tenants/me/a2a-directory"),
  /** D3: turn the public A2A directory listing on or off (admin only). */
  setA2ADirectory: (enabled: boolean) =>
    request<{ enabled: boolean }>("/tenants/me/a2a-directory", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  signup: (body: SignupRequest) =>
    request<TenantResponse>("/tenants/signup", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<TenantResponse>("/tenants/me"),
  listKeys: () => request<ApiKeyResponse[]>("/tenants/me/keys"),
  createKey: (name: string, scopes?: string[], roles?: string[]) =>
    request<{ raw_key: string; key_id: string; roles?: string[] }>(
      "/tenants/me/keys",
      {
        method: "POST",
        // roles omitted → backend default (operator, capped at the caller's roles)
        body: JSON.stringify({ name, scopes: scopes ?? [], ...(roles ? { roles } : {}) }),
      }
    ),
  revokeKey: (keyId: string) =>
    request<void>(`/tenants/me/keys/${keyId}`, { method: "DELETE" }),
  rotateKey: (keyId: string) =>
    request<{
      new_key: { raw_key: string; key_id: string };
      old_key_id: string;
      old_revoked: boolean;
      revoke_error?: string;
    }>(
      `/tenants/me/keys/${keyId}/rotate`,
      { method: "POST", body: JSON.stringify({ revoke_old: true }) }
    ),
  /** Get tenant LLM config (lightweight, no secrets) — Gap 1 */
  getLLMConfig: () => request<Record<string, unknown>>("/tenants/me/llm-config"),
  /** Save tenant LLM config (lightweight, no secret encryption) — Gap 1 */
  saveLLMConfig: (config: Record<string, unknown>) =>
    request<Record<string, unknown>>("/tenants/me/llm-config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  /** Get provider capabilities catalog — never returns secrets (Gap 3) */
  getProviders: () => request<{ providers: Array<{
    name: string;
    display_name: string;
    configured: boolean;
    capabilities: Record<string, boolean>;
    env_var: string | null;
  }> }>("/tenants/me/providers"),
};

// ── Governance ────────────────────────────────────────────────────────────────

export interface ApprovalRequest {
  request_id: string;
  goal_id: string;
  /** Org approval gates (mission tasks) carry their org. */
  org_id?: string | null;
  /** Optional server-provided kind; the inbox derives one when absent. */
  kind?: string;
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
  /** Hours (0-23, in `timezone`) the policy is active in — one entry per hour, not a range. */
  allowed_hours_utc?: number[];
  allowed_weekdays?: number[];
  /** IANA timezone the hours and weekdays are read in (default "UTC"). */
  timezone?: string;
}

export interface CreateGovernancePolicyRequest {
  name: string;
  description?: string;
  tools_pattern: string;
  action: "deny" | "require_approval";
  priority?: number;
  /** Hours (0-23, in `timezone`) the policy is active in — one entry per hour, not a range. */
  allowed_hours_utc?: number[];
  allowed_weekdays?: number[];
  /** IANA timezone for the window (default "UTC"). */
  timezone?: string;
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
  // Server-side stop state — the source of truth for the emergency banner.
  getEmergencyStop: () =>
    request<{
      active?: boolean;
      activated_at?: string | null;
      tenant_id?: string;
      activated_by?: string | null;
      cancelled_goals?: number | null;
      rejected_approvals?: number | null;
    }>(
      "/governance/emergency-stop"
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

// ── Billing (Razorpay) ────────────────────────────────────────────────────────

export interface RazorpayPlan {
  plan_id: string;
  name: string;
  prices: {
    monthly_inr: number;
    annual_inr: number;
    monthly_paise: number;
    annual_paise: number;
  };
  limits: Record<string, number>;
  razorpay_key_id: string;
}

export interface RazorpayOrder {
  order_id: string;
  amount: number;
  currency: string;
  plan: string;
  cycle: string;
  razorpay_key_id: string;
  is_mock: boolean;
  message?: string;
}

export interface RazorpayVerifyResult {
  status: string;
  plan: string;
  cycle: string;
  payment_id: string;
  message: string;
  limits: Record<string, number>;
}

export const billingApi = {
  getPlans: () => request<RazorpayPlan[]>('/billing/plans'),
  createOrder: (plan: string, cycle: 'monthly' | 'annual', currency = 'INR') =>
    request<RazorpayOrder>('/billing/create-order', {
      method: 'POST',
      body: JSON.stringify({ plan, cycle, currency }),
    }),
  verifyPayment: (data: {
    razorpay_order_id: string;
    razorpay_payment_id: string;
    razorpay_signature: string;
    plan: string;
    cycle: string;
  }) =>
    request<RazorpayVerifyResult>('/billing/verify-payment', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
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
  /** Matches the backend `IngestRequest.source_type` field exactly (freeform —
   *  e.g. "text" | "ocr" | "rpa-web" | any source label). */
  source_type?: string;
  metadata?: Record<string, unknown>;
}

export interface IngestResult {
  document_id: string;
  collection_id: string;
  chunks_created: number;
  content_hash: string;
}

export interface SearchResult {
  document_id: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
}

export interface RpaIngestRequest {
  collection_id: string;
  urls: string[];
  selector?: string;
  screenshot?: boolean;
  source_type?: string;
  max_chars?: number;
  include_links?: boolean;
}

export interface RpaIngestResult {
  url: string;
  success: boolean;
  chunks_ingested: number;
  total_chars?: number;
  playwright_used?: boolean;
  screenshot_captured?: boolean;
  links_extracted?: number;
  error?: string;
}

export interface RpaIngestResponse {
  collection_id: string;
  source_type: string;
  urls_processed: number;
  urls_succeeded: number;
  total_chunks_ingested: number;
  playwright_available: boolean;
  results: RpaIngestResult[];
}

export interface KnowledgeCitation {
  collection_id: string;
  chunk_id: string;
  score: number;
  source_url: string;
  excerpt: string;
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
    request<IngestResult>("/knowledge/ingest", { method: "POST", body: JSON.stringify(data) }),
  search: (collectionId: string, query: string, limit = 10) =>
    request<SearchResult[]>(
      `/knowledge/search?collection_id=${collectionId}&q=${encodeURIComponent(query)}&limit=${limit}`
    ),
  /** Ingest one or more URLs using Playwright (JS-rendered pages) */
  ingestRpaUrls: (data: RpaIngestRequest) =>
    request<RpaIngestResponse>("/knowledge/ingest/rpa-url", {
      method: "POST",
      body: JSON.stringify(data),
    }),
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

/** Analytics goals response — matches /analytics/goals endpoint.
 *  Distinct from GoalMetrics (governance type) which matches /goals/metrics.
 */
export interface AnalyticsGoalMetrics {
  period_days: number;
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  success_rate: number;
  avg_duration_s: number;
  avg_cost_usd: number;
  total_cost_usd: number;
  by_status: Record<string, number>;
}

export interface AnalyticsToolMetrics {
  period_days: number;
  tools: Array<{
    name: string;
    tool_name: string;
    total: number;
    call_count: number;
    failure_count: number;
    failure_rate: number;
    success: number;
    failed: number;
    success_rate: number;
    avg_latency_ms: number;
  }>;
}

export interface AnalyticsAgentMetrics {
  period_days: number;
  agents: Array<{
    agent_id: string;
    goal_count: number;
    success_rate: number;
    avg_eval_score: number;
    avg_cost_usd: number;
  }>;
}

export interface CostMetrics {
  period_days: number;
  total_cost_usd: number;
  cost_today_usd: number;
  goals_today: number;
  total_goals: number;
  avg_cost_per_goal: number;
  /** Daily cost breakdown — use this for trend charts */
  cost_by_day: Array<{ date: string; cost_usd: number }>;
  /** Cost broken down by model/tool */
  cost_by_model: Record<string, number>;
  /** Legacy key — same data as cost_by_day but with "period" key */
  trends: Array<{ period: string; cost_usd: number }>;
}

export interface EvalMetrics {
  total_evals: number;
  total: number;
  passed: number;
  pass_rate: number;
  avg_score: number;
  avg_scores: {
    task_completion: number;
    efficiency: number;
    accuracy: number;
    safety: number;
    coherence: number;
  };
  evals_by_day: Array<{ date: string; pass_rate: number; avg_score: number }>;
}

/**
 * GET /intelligence/benchmarks. Every figure is nullable: the backend returns
 * `null` (never an invented default) when it has no data, and platform averages
 * stay `null` until enough tenants contributed (k-anonymity guard) — in which
 * case `data_source` is `"insufficient_data"`.
 */
export interface BenchmarkMetrics {
  platform_avg_success_rate: number | null;
  platform_avg_cost_usd: number | null;
  platform_avg_eval_score: number | null;
  your_success_rate: number | null;
  your_cost_usd: number | null;
  your_eval_score: number | null;
  percentile_success: number | null;
  percentile_cost: number | null;
  /** "Top 10%" | "Top 25%" | "Average" | "Below Average" | "insufficient_data" */
  comparison_label: string;
  your_sample_count?: number;
  data_source?: BenchmarkDataSource;
  dimensions?: {
    your?: Record<string, number | null>;
    platform?: Record<string, number | null>;
  };
}

export type BenchmarkDataSource = "live_platform_data" | "insufficient_data";

/** GET /insights/benchmarks — platform-wide aggregate; all-null when there is not enough data. */
export interface InsightsBenchmarks {
  platform_avg_success_rate: number | null;
  platform_avg_cost_usd: number | null;
  platform_avg_duration_s?: number | null;
  platform_avg_iterations?: number | null;
  top_10_pct_success_rate: number | null;
  top_10_pct_cost_usd?: number | null;
  percentile_bands: Record<string, Record<string, number | null>>;
  sample_count?: number;
  data_source?: BenchmarkDataSource;
  message?: string;
}

export const analyticsApi = {
  getGoalMetrics: (days = 30) =>
    request<AnalyticsGoalMetrics>(`/analytics/goals?days=${days}`),
  getCostMetrics: (days = 30) =>
    request<CostMetrics>(`/analytics/costs?days=${days}`),
  getEvalMetrics: (days = 30) =>
    request<EvalMetrics>(`/analytics/evals?days=${days}`),
  getToolMetrics: (days = 30) =>
    request<AnalyticsToolMetrics>(`/analytics/tools?days=${days}`),
  getAgentMetrics: (days = 30) =>
    request<AnalyticsAgentMetrics>(`/analytics/agents?days=${days}`),
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
  avg_latency_ms?: number;
  last_used_at?: string | null;
  /** Below the success threshold (or blacklisted) — the executor deprioritises it. */
  unreliable?: boolean;
  blacklisted?: boolean;
  blacklist_reason?: string | null;
  /** When the self-improvement blacklist lapses (MEM-45). */
  blacklist_expires_at?: string | null;
  [key: string]: unknown;
}

/** Canonical governed memory kinds (backend `MemoryKind`). */
export type MemoryKind =
  | "execution" | "reflexion" | "long_term" | "episodic"
  | "procedural" | "knowledge_graph" | "prospective";

/** One row from GET /memory/records (canonical `memory_records` table). */
export interface MemoryRecordItem {
  memory_id: string;
  memory_kind: MemoryKind;
  content: string;
  source_goal_id: string;
  source_execution_id: string;
  classification: string;
  confidence: number;
  lifecycle_state: string;
  evidence_refs: string[];
  recall_count: number;
  helpful_count: number;
  harmful_count: number;
  expires_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** A deferred intention (prospective memory) — runs as a goal when due. */
export interface ProspectiveIntention {
  id: string;
  intention: string;
  due_at: string;
  expires_at: string;
  state: string;
  /** Fire attempts so far; an intention that keeps failing ends in state "failed". */
  attempts?: number;
  source_goal_id: string;
  agent_id: string | null;
  result: Record<string, unknown> | null;
}

export interface MemoryRecordsResponse {
  records: MemoryRecordItem[];
  total: number;
  kinds: Record<string, number>;
}

export const memoryApi = {
  list: (opts: { limit?: number; offset?: number; memoryType?: string } = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(opts.limit ?? 50));
    if (opts.offset) params.set("offset", String(opts.offset));
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
  update: (id: string, data: Partial<MemoryEntry>) =>
    request<MemoryEntry>(`/memory/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  clearAll: () => request<void>("/memory", { method: "DELETE" }),
  toolReliability: () => request<ToolReliabilityRow[]>("/memory/tool-reliability"),
  /** Lift a tool's blacklist now (admin only, audited; 403 otherwise). */
  clearToolBlacklist: (toolName: string) =>
    request<void>(`/memory/tool-reliability/${encodeURIComponent(toolName)}/blacklist`, {
      method: "DELETE",
    }),
  listExecution: (params: { limit?: number; offset?: number } = {}) =>
    request<Array<{ goal_text: string; success: boolean; recorded_at: string }>>(
      `/memory/execution?limit=${params.limit ?? 50}&offset=${params.offset ?? 0}`,
    ),
  listIntentions: (opts: { includeFailed?: boolean } = {}) =>
    request<ProspectiveIntention[]>(
      opts.includeFailed ? "/memory/prospective?include_failed=true" : "/memory/prospective",
    ),
  createIntention: (data: { intention: string; due_at: string; expires_at?: string }) =>
    request<ProspectiveIntention>("/memory/prospective", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  cancelIntention: (id: string) =>
    request<void>(`/memory/prospective/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** Canonical governed records categorized by memory_kind with goal-linkage + TTL. */
  listRecords: (opts: { kind?: MemoryKind; goalId?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(opts.limit ?? 50));
    if (opts.kind) params.set("kind", opts.kind);
    if (opts.goalId) params.set("goal_id", opts.goalId);
    return request<MemoryRecordsResponse>(`/memory/records?${params.toString()}`);
  },
};

// ── Knowledge Graph (tenant KG — app/api/knowledge_graph.py) ──────────────────

/** One of the backend's `NodeType` enum values (app/knowledge_graph/models.py). */
export type KGNodeType =
  | "document" | "chunk" | "entity" | "concept" | "goal"
  | "tool" | "memory" | "artifact" | "agent" | "workflow";

/** One of the backend's `EdgeType` enum values. */
export type KGEdgeType =
  | "mentions" | "supports" | "contradicts" | "caused_by" | "depends_on"
  | "used_tool" | "produced_artifact" | "similar_to" | "parent_of" | "references";

/** Node shape as returned by GET /knowledge-graph/export (summary fields only). */
export interface KGNode {
  node_id: string;
  node_type: KGNodeType;
  label: string;
  confidence: number;
  source_id?: string | null;
}

/** Edge shape as returned by GET /knowledge-graph/export. */
export interface KGEdge {
  edge_id: string;
  edge_type: KGEdgeType;
  source: string;
  target: string;
  confidence: number;
}

export interface KGGraph {
  tenant_id: string;
  exported_at: string;
  nodes: KGNode[];
  edges: KGEdge[];
  stats: { nodes: number; edges: number };
  format: string;
}

export interface KGStats {
  total_nodes: number;
  total_edges: number;
  node_types: Record<string, number>;
  avg_confidence: number;
}

/** Full node detail (content/metadata) plus its incident edges — GET /knowledge-graph/nodes/{id}. */
export interface KGNodeDetail {
  node: {
    node_id: string;
    node_type: KGNodeType;
    label: string;
    content: string;
    confidence: number;
    source_id: string | null;
    metadata: Record<string, unknown>;
  };
  edges: Array<{
    edge_id: string;
    edge_type: KGEdgeType;
    source_node_id: string;
    target_node_id: string;
    label: string;
    confidence: number;
    evidence: string;
  }>;
}

export const knowledgeGraphApi = {
  /** Full node+edge export for the tenant — the data source for the graph view. */
  getGraph: () => request<KGGraph>("/knowledge-graph/export"),
  getStats: () => request<KGStats>("/knowledge-graph/stats"),
  getNode: (nodeId: string) => request<KGNodeDetail>(`/knowledge-graph/nodes/${nodeId}`),
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

/** Response shape for paginated artifact listings. */
export interface PaginatedArtifacts {
  items: Artifact[];
  total: number;
}

/** Union returned by artifactsApi.list — backend may return either shape. */
export type ArtifactListResponse = Artifact[] | PaginatedArtifacts;

export const artifactsApi = {
  list: (opts: {
    goalId?: string;
    artifactType?: string;
    /** Alias for artifactType — used by paginated callers. */
    type?: string;
    limit?: number;
    offset?: number;
    search?: string;
  } = {}) => {
    const params = new URLSearchParams();
    if (opts.goalId) params.set("goal_id", opts.goalId);
    const artifactType = opts.type ?? opts.artifactType;
    if (artifactType) params.set("artifact_type", artifactType);
    params.set("limit", String(opts.limit ?? 50));
    if (opts.offset) params.set("offset", String(opts.offset));
    if (opts.search) params.set("search", opts.search);
    return request<ArtifactListResponse>(`/artifacts?${params.toString()}`);
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

/** Tenant workspace totals and limits (GET /tools/workspace/usage). */
export interface WorkspaceUsage {
  bytes_used: number;
  entries: number;
  max_file_bytes: number;
  max_tenant_bytes: number;
  max_entries: number;
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
  workspaceUsage: () => request<WorkspaceUsage>("/tools/workspace/usage"),
  sendEmail: (body: {
    to: string | string[];
    subject: string;
    body: string;
    from_addr?: string;
    cc?: string;
  }) =>
    request<{ success?: boolean; quota_remaining?: number; quota_limit?: number } & Record<string, unknown>>("/tools/email/send", {
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

export interface TrainingExportJob {
  job_id: string;
  status: "queued" | "running" | "complete" | "failed" | "expired";
  format: "openai" | "anthropic";
  min_score: number | null;
  limit: number;
  example_count: number | null;
  has_file: boolean;
  error: string | null;
  created_at: string | null;
  started_at?: string | null;
  completed_at: string | null;
  download_url?: string;
}

/** Non-blank lines of a JSONL body, without splitting it into an array. */
function countJsonlLines(text: string): number {
  let count = 0;
  let start = 0;
  while (start <= text.length) {
    const end = text.indexOf("\n", start);
    const stop = end === -1 ? text.length : end;
    if (text.slice(start, stop).trim() !== "") count += 1;
    if (end === -1) break;
    start = end + 1;
  }
  return count;
}

export const trainingApi = {
  export: async (opts: {
    format: "openai" | "anthropic";
    minScore?: number;
    limit?: number;
    fromDate?: string;
    toDate?: string;
  }): Promise<{ blob: Blob; filename: string; count: number }> => {
    const params = new URLSearchParams();
    params.set("format", opts.format);
    params.set("min_score", String(opts.minScore ?? 0.8));
    params.set("limit", String(opts.limit ?? 1000));
    if (opts.fromDate) params.set("from_date", opts.fromDate);
    if (opts.toDate) params.set("to_date", opts.toDate);
    const apiKey = getApiKey();
    const headers: Record<string, string> = { ...getMfaHeader() };
    if (apiKey) headers["X-API-Key"] = apiKey;
    const res = await fetch(
      `${API_BASE_URL}/intelligence/export-training-data?${params.toString()}`,
      { method: "POST", headers }
    );
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    // The DB-backed export is streamed, so the server cannot send a count
    // header up front; count the JSONL lines instead.
    const header = res.headers.get("X-Training-Examples");
    let blob: Blob;
    let count: number;
    if (header !== null) {
      blob = await res.blob();
      count = Number(header);
    } else {
      const text = await res.text();
      blob = new Blob([text], { type: "application/x-ndjson" });
      count = countJsonlLines(text);
    }
    return {
      blob,
      filename: parseFilename(
        res.headers.get("Content-Disposition"),
        `training_${opts.format}.jsonl`
      ),
      count,
    };
  },

  /** Queue a durable background export (large exports; written to object storage). */
  createJob: (opts: { format: "openai" | "anthropic"; minScore?: number; limit?: number }) => {
    const params = new URLSearchParams();
    params.set("format", opts.format);
    params.set("min_score", String(opts.minScore ?? 0.8));
    params.set("limit", String(opts.limit ?? 10000));
    return request<TrainingExportJob>(
      `/intelligence/export-training-data/jobs?${params.toString()}`,
      { method: "POST" }
    );
  },

  // Polled by the jobs panel, which renders a 5xx inline (no toast per poll).
  listJobs: () =>
    request<{ jobs: TrainingExportJob[] }>(
      "/intelligence/export-training-data/jobs",
      {},
      { silenceServerErrorToast: true }
    ),

  getJob: (jobId: string) =>
    request<TrainingExportJob>(
      `/intelligence/export-training-data/jobs/${encodeURIComponent(jobId)}`,
      {},
      { silenceServerErrorToast: true }
    ),

  /** Download a finished job's JSONL through the API (owning tenant only). */
  downloadJob: async (jobId: string): Promise<{ blob: Blob; filename: string }> => {
    const apiKey = getApiKey();
    const headers: Record<string, string> = { ...getMfaHeader() };
    if (apiKey) headers["X-API-Key"] = apiKey;
    const res = await fetch(
      `${API_BASE_URL}/intelligence/export-training-data/jobs/${encodeURIComponent(jobId)}/download`,
      { headers }
    );
    if (!res.ok) {
      // 409 = the job has no file (queued / running / failed); say which.
      const body: unknown = await res.json().catch(() => undefined);
      throw new ApiError(res.status, errorMessageFromBody(body) ?? res.statusText, body);
    }
    return {
      blob: await res.blob(),
      filename: parseFilename(
        res.headers.get("Content-Disposition"),
        `agentverse_training_${jobId}.jsonl`
      ),
    };
  },

  preview: (opts: { minScore?: number; limit?: number; from_date?: string; to_date?: string }): Promise<TrainingPreview> => {
    const params = new URLSearchParams();
    params.set("min_score", String(opts.minScore ?? 0.8));
    params.set("limit", String(opts.limit ?? 1000));
    if (opts.from_date) params.set("from_date", opts.from_date);
    if (opts.to_date) params.set("to_date", opts.to_date);
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

/** A tenant's registered remote A2A agent (server-side registry, RLS-scoped). */
export interface RemoteA2AAgent {
  id: string;
  name: string;
  url: string;
  card: AgentCard | null;
  last_error: string | null;
  last_checked_at?: string | null;
  created_at?: string | null;
}

export const a2aApi = {
  agentCard: () => request<AgentCard>("/.well-known/agent.json"),
  /** Remote agents: the server fetches + validates the agent card (SSRF-guarded). */
  listRemoteAgents: () => request<{ agents: RemoteA2AAgent[] }>("/a2a/remote-agents"),
  registerRemoteAgent: (data: { url: string; name?: string }) =>
    request<RemoteA2AAgent>("/a2a/remote-agents", { method: "POST", body: JSON.stringify(data) }),
  pingRemoteAgent: (id: string) =>
    request<RemoteA2AAgent>(`/a2a/remote-agents/${id}/ping`, { method: "POST" }),
  deleteRemoteAgent: (id: string) =>
    request<void>(`/a2a/remote-agents/${id}`, { method: "DELETE" }),
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

/** A tenant's claim on an external channel (GET /channels/mappings). */
export interface ChannelBinding {
  id: string;
  channel_type: string;
  channel_id: string;
  status?: string;
  verified_at?: string | null;
}

/** TRG-36: a Slack user linked to (or a pending code for) an AgentVerse key. */
export interface SlackIdentityLink {
  id: string;
  principal_id: string;
  team_id: string | null;
  slack_user_id: string | null;
  status: "pending" | "active";
  code_expires_at?: string | null;
  linked_at?: string | null;
}

export interface SlackLinkCode {
  id: string;
  status: "pending";
  code: string;
  expires_at: string;
  instructions: string;
}

export const integrationsApi = {
  zapierCompletedGoals: () =>
    request<ZapierCompletedGoal[]>("/integrations/zapier/goals"),
  slackIdentities: () => request<SlackIdentityLink[]>("/channels/identities"),
  createSlackLinkCode: () =>
    request<SlackLinkCode>("/channels/identities/link-codes", {
      method: "POST",
      body: JSON.stringify({ channel_type: "slack" }),
    }),
  deleteSlackIdentity: (id: string) =>
    request<{ id: string; deleted: boolean }>(
      `/channels/identities/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  /** TRG-02: Slack commands route by the workspace's verified binding. */
  slackWorkspaces: async () =>
    (await request<ChannelBinding[]>("/channels/mappings")).filter(
      (m) => m.channel_type === "slack",
    ),
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
  created_at?: string;
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
  /** Bumped by every golden-task add / edit / delete / import (MEM-54). */
  dataset_version?: number;
  /** The suite's newest run (null when it never ran); a running one has live progress. */
  last_run?: EvalSuiteRunSummary | null;
}

/**
 * Lifecycle of a durable suite run (MEM-53). "abandoned": no worker has made
 * progress for a while (the stalled-run sweeper re-dispatches it); "failed":
 * the run itself errored (not a task verdict).
 */
export type EvalSuiteRunStatus = "running" | "completed" | "failed" | "abandoned";

/** Per-task progress of a run, from its persisted task rows. */
export interface EvalSuiteRunProgress {
  total: number;
  done: number;
  passed: number;
  failed: number;
  unscored: number;
  running: number;
  pending: number;
}

export interface EvalSuiteRunSummary {
  run_id: string;
  status?: EvalSuiteRunStatus | string;
  total?: number | null;
  passed?: number | null;
  failed?: number | null;
  pass_rate?: number | null;
  run_at?: string | null;
  finished_at?: string | null;
  dataset_version?: number | null;
  /** The agent the golden goals ran on (the one being promoted), if any. */
  agent_id?: string | null;
  progress?: EvalSuiteRunProgress;
}

export interface GoldenTask {
  task_id: string;
  goal: string;
  expected_tools: string[];
  forbidden_tools: string[];
  expected_output_contains: string[];
  expected_output: string;
  min_score: number;
  max_iterations: number;
  tags: string[];
  /** Sources a run must cite (a scored check). */
  expected_citations?: string[];
  /** The goal this task was promoted from, if any. */
  source_goal_id?: string;
  /** The dataset version this revision of the task was written in. */
  revision?: number;
}

export interface EvalSuiteDetail extends EvalSuite {
  tasks: GoldenTask[];
  tasks_truncated?: boolean;
}

export interface GoldenDatasetExport {
  format: string;
  suite_id: string;
  name: string;
  dataset_version: number;
  task_count: number;
  tasks: GoldenTask[];
}

export interface EvalSuiteResult extends EvalSuiteRunSummary {
  suite_id?: string;
  passed: number;
  failed: number;
  error?: string | null;
  /** The golden dataset version the run executed. */
  dataset_version?: number | null;
  /** A summary (failures first); page the rest via getRunTasks. */
  task_results?: Array<EvalSuiteTaskResult>;
}

export interface EvalSuiteTaskResult {
  task_id: string;
  passed: boolean;
  /**
   * "timeout" / "error": the goal never finished (or the judge failed), so the
   * task was not scored. "invalid": the task has no checks.
   */
  status?: "scored" | "timeout" | "error" | "invalid";
  failure_reasons?: string[];
  goal_id?: string | null;
  /** goal_complete / goal_failed / goal_cancelled / goal_rejected */
  terminal_event?: string | null;
  /** Judge overall score, or the fraction of checks passed. */
  score?: number | null;
  judge?: { overall?: number; reasoning?: string; llm_judged?: boolean } | null;
}

export interface GoldenTaskInput {
  task_id?: string;
  goal: string;
  expected_tools?: string[];
  forbidden_tools?: string[];
  expected_output_contains?: string[];
  expected_output?: string;
  min_score?: number;
  max_iterations?: number;
  tags?: string[];
  expected_citations?: string[];
}

/** A golden task must check something (MEM-51); the API refuses one that doesn't. */
export function goldenTaskHasChecks(task: GoldenTaskInput): boolean {
  return Boolean(
    (task.expected_tools ?? []).some((t) => t.trim()) ||
      (task.forbidden_tools ?? []).some((t) => t.trim()) ||
      (task.expected_output_contains ?? []).some((t) => t.trim()) ||
      (task.expected_citations ?? []).some((t) => t.trim()) ||
      (task.expected_output ?? "").trim(),
  );
}

/** Options for promoting a completed goal to a golden task (a10-F235-01). */
export interface PromoteGoalOptions {
  /** Appended to the goal text as the task input. */
  context?: string;
  tags?: string[];
  /** Expect the tools the run called (default true). */
  include_tools?: boolean;
  /** Expect the sources the run cited (default true). */
  include_citations?: boolean;
  expected_output_contains?: string[];
  min_score?: number;
  max_iterations?: number;
}

export interface PromoteGoalResult {
  suite_id: string;
  goal_id: string;
  task_id: string;
  /** The new dataset version the task was added in. */
  dataset_version: number;
  task: GoldenTask;
}

export const evalSuitesApi = {
  listSuites: () => request<EvalSuite[]>("/intelligence/eval-suites"),
  createSuite: (name: string, description?: string) =>
    request<EvalSuite>("/intelligence/eval-suites", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  getSuite: (id: string) => request<EvalSuiteDetail>(`/intelligence/eval-suites/${id}`),
  addTask: (suiteId: string, task: GoldenTaskInput) =>
    request<void>(`/intelligence/eval-suites/${suiteId}/tasks`, {
      method: "POST",
      body: JSON.stringify(task),
    }),
  /**
   * Promote a completed goal to a golden task of the suite (a new dataset version):
   * its verified answer, the tools it called and the sources it cited become the
   * expectation. 409 when already promoted into this suite; 422 when not promotable.
   */
  promoteGoal: (suiteId: string, goalId: string, options: PromoteGoalOptions = {}) =>
    request<PromoteGoalResult>(
      `/intelligence/eval-suites/${encodeURIComponent(suiteId)}/tasks/from-goal/${encodeURIComponent(goalId)}`,
      { method: "POST", body: JSON.stringify(options) },
    ),
  /** With ``agentId`` every golden goal runs on that agent and the run can vouch for it in the rollout gate. */
  runSuite: (id: string, agentId?: string) =>
    request<{ run_id: string; dataset_version?: number; agent_id?: string | null }>(
      `/intelligence/eval-suites/${id}/run`,
      { method: "POST", body: JSON.stringify(agentId ? { agent_id: agentId } : {}) },
    ),
  /** Newest first. */
  getSuiteResults: (id: string) =>
    request<EvalSuiteResult[]>(`/intelligence/eval-suites/${id}/results`),
  deleteSuite: (suiteId: string) =>
    request<void>(`/intelligence/eval-suites/${suiteId}`, { method: 'DELETE' }),
  updateTask: (suiteId: string, taskId: string, changes: Partial<GoldenTaskInput>) =>
    request<{ dataset_version: number; task: GoldenTask }>(
      `/intelligence/eval-suites/${suiteId}/tasks/${encodeURIComponent(taskId)}`,
      { method: "PATCH", body: JSON.stringify(changes) },
    ),
  deleteTask: (suiteId: string, taskId: string) =>
    request<{ dataset_version: number }>(
      `/intelligence/eval-suites/${suiteId}/tasks/${encodeURIComponent(taskId)}`,
      { method: "DELETE" },
    ),
  exportDataset: (suiteId: string, version?: number) =>
    request<GoldenDatasetExport>(
      `/intelligence/eval-suites/${suiteId}/export${version != null ? `?version=${version}` : ""}`,
    ),
  importDataset: (suiteId: string, tasks: GoldenTaskInput[], replace = false) =>
    request<{ dataset_version: number; imported: number }>(
      `/intelligence/eval-suites/${suiteId}/import`,
      { method: "POST", body: JSON.stringify({ tasks, replace }) },
    ),
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
    request<{ run_id: string; status: string; steps_executed?: number; waves?: number; summary?: string }>(
      `/workflows/${id}/run${dryRun ? "?dry_run=true" : ""}`,
      { method: "POST" }
    ),
  generate: (goal: string) =>
    request<{
      nodes: Array<{
        id: string; type: string; label: string; subtitle?: string;
        position: { x: number; y: number }; tool?: string;
        depends_on?: string[]; can_parallel?: boolean;
      }>;
      edges: Array<{ id?: string; source: string; target: string }>;
    }>("/workflows/generate", { method: "POST", body: JSON.stringify({ goal }) }),
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
  request_id?: string;
  /** ready | failed (a failed export carries `error` + `failed_sections`, no download_url). */
  status?: string;
  download_url?: string | null;
  error?: string | null;
  /** Section name -> why it could not be exported (failed exports only). */
  failed_sections?: Record<string, string>;
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

// ── Prompt Variants (PromptOptimizer A/B testing) ─────────────────────────────

export interface PromptVariantItem {
  id: string;
  key: string;
  name: string;
  prompt_text: string;
  is_control: boolean;
  run_count: number;
  mean_score: number | null;
  p95_score: number | null;
  promoted_at: string | null;
}

export interface PromptVariantReport {
  id: string;
  key: string;
  name: string;
  mean_score: number | null;
  p95_score: number | null;
  run_count: number;
  /** P(variant beats its control), from recorded scores; null = not computed. */
  win_rate: number | null;
  /** 1 - two-sided p-value of the comparison with the control; null = not computed. */
  statistical_significance: number | null;
  /** The control variant the comparison was made against. */
  compared_to?: string | null;
}

export const promptVariantsApi = {
  list: (key: string = "") =>
    request<PromptVariantItem[]>(
      key
        ? `/intelligence/prompt-variants?key=${encodeURIComponent(key)}`
        : "/intelligence/prompt-variants"
    ),
  create: (data: { key: string; name: string; prompt_text: string }) =>
    request<PromptVariantItem>("/intelligence/prompt-variants", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  promote: (id: string) =>
    request<{ id: string; key: string; promoted: boolean; promoted_at: string }>(
      `/intelligence/prompt-variants/${id}/promote`,
      { method: "POST" }
    ),
  delete: (id: string) =>
    request<void>(`/intelligence/prompt-variants/${id}`, { method: "DELETE" }),
  report: (id: string) =>
    request<PromptVariantReport>(`/intelligence/prompt-variants/${id}/report`),
};

// ── Red Team API ───────────────────────────────────────────────────────────────

export interface RedTeamResult {
  report_id: string;
  total: number;
  passed: number;
  failed: number;
  run_at: string;
  results: Array<{ case: string; passed: boolean; details?: string }>;
}

export const redTeamApi = {
  run: (cases?: string[]) =>
    request<RedTeamResult>("/enterprise/red-team", {
      method: "POST",
      body: JSON.stringify({ cases: cases ?? null }),
    }),
};

// ── Insights API ──────────────────────────────────────────────────────────────

export interface EstimateBand { min: number; mean: number; max: number }

/** POST /insights/estimate — derived from the tenant's similar finished goals.
 *  Every band is null when no similar goal carries that data (never a default). */
export interface CostEstimate {
  estimated_cost_usd: EstimateBand | null;
  estimated_duration_s: EstimateBand | null;
  estimated_iterations: EstimateBand | null;
  success_probability: number | null;
  similar_goals_count: number;
  confidence: "none" | "low" | "medium" | "high";
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
  cost_usd: number | null;
}

/** GET /insights/agent-health/{id} — each axis is null when the agent has no
 *  data behind it (no finished runs, no scorecards, no tool calls). */
export interface AgentHealth {
  agent_id: string;
  health: {
    speed: number | null;
    accuracy: number | null;
    cost_efficiency: number | null;
    tool_coverage: number | null;
    success_rate: number | null;
    coherence: number | null;
  };
  sample_size: number;
  finished_count?: number;
  eval_sample_size?: number;
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
  getBenchmarks: () => request<InsightsBenchmarks>("/insights/benchmarks"),
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
  list: (domain?: string, search?: string) => {
    const params = new URLSearchParams();
    if (domain) params.set("domain", domain);
    if (search) params.set("search", search);
    const qs = params.toString();
    return request<GoalTemplate[]>(`/templates${qs ? `?${qs}` : ""}`);
  },
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
  /** V2 — paginated listing with optional search, domain filter, and sort order */
  list: (params: { domain?: string; search?: string; page?: number; page_size?: number; sort_by?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.domain) q.set("domain", params.domain);
    if (params.search) q.set("search", params.search);
    if (params.page != null) q.set("page", String(params.page));
    if (params.page_size != null) q.set("page_size", String(params.page_size));
    if (params.sort_by) q.set("sort_by", params.sort_by);
    const qs = q.toString();
    return request<{ templates?: MarketplaceV2Template[]; items?: MarketplaceV2Template[]; total: number; page: number; page_size: number }>(
      `/marketplace/templates${qs ? `?${qs}` : ""}`
    );
  },
  get: (id: string) => request<MarketplaceV2Template>(`/marketplace/templates/${id}`),
  /** The caller tenant's installs (DB-backed, RLS-scoped) — the source for "installed" markers. */
  listInstalls: () =>
    request<{ installed_ids: string[]; installs: Array<Record<string, unknown>> }>("/marketplace/installs"),
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
  search: (query: string, domain?: string) =>
    request<{ items: MarketplaceV2Template[]; total: number }>(
      "/marketplace/search",
      { method: "POST", body: JSON.stringify({ query, domain, page_size: 20 }) }
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

/** One row of GET /agents/{id}/credentials (AgentIdentityService.list_credentials).
 *  Every credential is an RS256 service-account keypair; only the public half is stored. */
export interface AgentCredential {
  id: string;
  agent_id: string;
  key_id: string;
  key_type: string;
  scopes: string[];
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_by: string;
  created_at: string;
  description: string;
}

/** Scopes an agent credential may hold (backend ROLE_SCOPES["agent"]); anything else is a 422. */
export const AGENT_CREDENTIAL_SCOPES = [
  "goals:read", "goals:write", "goals:execute",
  "agents:read",
  "knowledge:read", "knowledge:write",
  "mcp:read", "tools:read",
  "a2a:read", "a2a:write",
  "artifacts:read", "artifacts:write",
  "memory:read", "memory:write",
  "rpa:read", "perception:read", "guardrails:read",
] as const;

export interface IssueCredentialRequest {
  scopes: string[];
  expires_in_days?: number;
  description?: string;
}

/** POST /agents/{id}/credentials (201). The private key is returned ONCE and never stored. */
export interface IssuedCredential {
  key_id: string;
  private_key_pem: string;
  public_key_pem: string;
  scopes: string[];
  expires_at: string | null;
  warning?: string;
}

export type CredentialStatus = "active" | "revoked" | "expired";

export function credentialStatus(c: Pick<AgentCredential, "revoked_at" | "expires_at">): CredentialStatus {
  if (c.revoked_at) return "revoked";
  if (c.expires_at && new Date(c.expires_at) < new Date()) return "expired";
  return "active";
}

/** Save a just-issued private key as a .pem file (it is shown only once). */
export function downloadPrivateKey(cred: Pick<IssuedCredential, "key_id" | "private_key_pem">): void {
  const blob = new Blob([cred.private_key_pem], { type: "application/x-pem-file" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${cred.key_id}.pem`;
  a.click();
  URL.revokeObjectURL(url);
}

export const credentialsApi = {
  list: (agentId: string) => request<AgentCredential[]>(`/agents/${agentId}/credentials`),
  issue: (agentId: string, req: IssueCredentialRequest) =>
    request<IssuedCredential>(`/agents/${agentId}/credentials`, { method: "POST", body: JSON.stringify(req) }),
  revoke: (agentId: string, keyId: string) =>
    request<void>(`/agents/${agentId}/credentials/${keyId}`, { method: "DELETE" }),
  // No browser token exchange: POST /agents/{id}/token needs a client assertion signed
  // with the credential's private key (RFC 7523), which only the agent holds.
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
  layer?: string;
  action_taken?: string;
  goal_id?: string;
  agent_id?: string;
  created_at: string;
}

/** A row of GET /guardrails-v2/violations (the durable, fleet-wide store). */
export interface GuardrailV2Violation {
  violation_id: string;
  rule_id?: string;
  rule_name: string;
  layer: string;
  action_taken: string;
  category: string;
  severity: string;
  goal_id?: string | null;
  content_preview?: string;
  created_at: string;
}

export function toGuardrailViolation(v: GuardrailV2Violation): GuardrailViolation {
  const where = `${v.action_taken} · ${v.layer}`;
  return {
    id: v.violation_id,
    guardrail_id: v.rule_id ?? "",
    guardrail_name: v.rule_name,
    type: v.category,
    severity: v.severity,
    message: v.content_preview ? `${where} — ${v.content_preview}` : where,
    layer: v.layer,
    action_taken: v.action_taken,
    goal_id: v.goal_id ?? undefined,
    created_at: v.created_at,
  };
}

export interface GuardrailStats {
  total_24h: number;
  /** Kept for older servers; the window total on current ones. */
  total_all: number;
  total_window?: number;
  window_days?: number;
  by_severity: Record<string, number>;
  by_layer: Record<string, number>;
  top_categories: Array<{ category: string; count: number }>;
  /** null: durable violations carry no risk score. */
  risk_score_p95: number | null;
}

export const guardrailsApi = {
  list: () =>
    request<{ configs: GuardrailConfig[]; total: number } | GuardrailConfig[]>("/guardrails").then(
      (res) => (Array.isArray(res) ? res : res.configs ?? [])
    ),
  create: (body: CreateGuardrailRequest) =>
    request<GuardrailConfig>("/guardrails", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: Partial<CreateGuardrailRequest> & { enabled?: boolean }) =>
    request<void>(`/guardrails/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  delete: (id: string) => request<void>(`/guardrails/${id}`, { method: "DELETE" }),
  test: (body: { text: string; rule_id?: string; layer?: string }) =>
    request<GuardrailTestResult>("/guardrails/test", { method: "POST", body: JSON.stringify(body) }),
  // P8b-3: the durable v2 store. The legacy /guardrails/violations read a
  // per-process dict nothing wrote to, so this tab always said "No violations".
  getViolations: (params?: { limit?: number; severity?: string; goal_id?: string; cursor?: string }) => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.goal_id) qs.set("goal_id", params.goal_id);
    if (params?.cursor) qs.set("cursor", params.cursor);
    const q = qs.toString();
    return request<{ violations: GuardrailV2Violation[]; next_cursor?: string | null } | GuardrailV2Violation[]>(
      `/guardrails-v2/violations${q ? `?${q}` : ""}`
    ).then(
      (res) => (Array.isArray(res) ? res : res.violations ?? []).map(toGuardrailViolation)
    );
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
  /** agent_name is not returned by the backend; use agent_id for display. */
  agent_name?: string;
  total_cost_usd: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  goal_count: number;
  avg_cost_per_goal: number;
}

export interface CostPrediction {
  /** p50 predicted cost in USD */
  predicted_cost_usd: number;
  /** p95 predicted cost in USD */
  p95_cost_usd: number;
  confidence: "low" | "medium" | "high";
  basis: string;
  breakdown: {
    planning_usd: number;
    execution_usd: number;
    verification_usd: number;
  };
  budget_remaining_usd: number;
}

export interface CostModelBreakdown {
  model: string;
  total_cost_usd: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  call_count: number;
}

export interface CostTrend {
  date: string;
  cost_usd: number;
  moving_avg_7d: number;
  is_anomaly: boolean;
}

export interface CostProjection {
  projected_monthly_usd: number;
  daily_avg_usd: number;
  days_of_data: number;
  confidence: "low" | "high";
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
  getSummary: (periodDays = 30) => request<CostSummary>(`/costs/summary?period_days=${periodDays}`),
  getSummaryCsv: (periodDays = 30): Promise<Blob> => {
    const apiKey = getApiKey();
    const headers: Record<string, string> = { ...getMfaHeader() };
    if (apiKey) headers["X-API-Key"] = apiKey;
    return fetch(`${API_BASE_URL}/costs/summary?format=csv&period_days=${periodDays}`, { headers })
      .then((r) => r.blob());
  },
  getPerAgent: (periodDays = 30) =>
    request<{ agents: AgentCost[]; period_days?: number }>(`/costs/per-agent?period_days=${periodDays}`).then(
      (res) => res.agents ?? []
    ) as Promise<AgentCost[]>,
  getCostByModel: (periodDays = 30) =>
    request<{ models: CostModelBreakdown[]; period_days?: number }>(`/costs/by-model?period_days=${periodDays}`).then(
      (res) => res.models ?? []
    ) as Promise<CostModelBreakdown[]>,
  getCostTrends: (periodDays = 30) =>
    request<{ trends: CostTrend[]; period_days?: number }>(`/costs/trends?period_days=${periodDays}`).then(
      (res) => res.trends ?? []
    ) as Promise<CostTrend[]>,
  getProjection: () => request<CostProjection>("/costs/projection"),
  predict: (goalDescription: string, agentId?: string) =>
    request<CostPrediction>("/costs/predict", {
      method: "POST",
      body: JSON.stringify({ goal_description: goalDescription, agent_id: agentId ?? null }),
    }),
  getBudgets: () =>
    request<{ daily_limit?: number; per_goal_usd?: number; per_tenant_daily_usd?: number; per_agent_daily_usd?: Record<string, number>; budget_pct_remaining?: number; daily_spent?: number; daily_remaining?: number }>("/costs/budgets"),
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
        severity: a.severity ?? (a.sigma_deviation >= 4 ? "high" : a.sigma_deviation >= 2.5 ? "medium" : "low") as "low" | "medium" | "high",
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
  /**
   * @deprecated The backend answers 410 Gone: v1 suggestions never changed any
   * agent. Apply a concluded experiment's winner with {@link applyExperiment}
   * (POST /intelligence/experiments/{id}/apply). Kept only so a caller gets the
   * backend's explanatory 410 message as an ApiError rather than a 404.
   */
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
  /**
   * Manually apply a concluded experiment's winning candidate config — the
   * human-in-the-loop half of the self-improvement loop, used when autonomous
   * auto-apply is disabled (the default). Fails closed on the backend: 404 for
   * unknown experiments, 409 when not an applicable winner.
   */
  applyExperiment: (id: string) =>
    request<{ experiment_id: string; agent_id: string; status: string }>(
      `/intelligence/experiments/${id}/apply`,
      { method: "POST" }
    ),
  getBenchmarks: (days = 30) =>
    request<BenchmarkMetrics>(`/intelligence/benchmarks?days=${days}`),
  /** Return all 7 eval dimension names from /intelligence/eval/dimensions */
  getEvalDimensions: () =>
    request<{ dimensions: string[]; count: number }>("/intelligence/eval/dimensions"),
};

// ── Observability (spans / traces / logs) ─────────────────────────────────────

export interface SpanRecord {
  name: string;
  trace_id: string;
  span_id: string;
  start_time: number;
  end_time: number;
  attributes: Record<string, unknown>;
  status: string;
  parent_span_id?: string;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error' | 'debug';
  message: string;
  source?: string;
  goal_id?: string;
  agent_id?: string;
}

export const observabilityApi = {
  /** Fetch recent in-process trace spans for the waterfall viewer. */
  getSpans: (limit = 100, params?: { since?: string; until?: string }) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (params?.since) qs.set('since', params.since);
    if (params?.until) qs.set('until', params.until);
    return request<SpanRecord[]>(`/analytics/observability/spans?${qs.toString()}`);
  },
  /** Fetch structured observability metrics (latency percentiles, etc.). */
  getMetrics: (params?: { since?: string; until?: string }) => {
    const qs = new URLSearchParams();
    if (params?.since) qs.set('since', params.since);
    if (params?.until) qs.set('until', params.until);
    const q = qs.toString();
    return request<{
      goal_duration_percentiles?: { p50?: number; p95?: number; p99?: number };
      latency_percentiles?: { p50?: number; p95?: number; p99?: number };
      [key: string]: unknown;
    }>(`/observability/metrics${q ? `?${q}` : ''}`);
  },
  /** Fetch time-series data bucketed by hour (or minute/day) for trend charts. */
  getTimeSeries: (params: { since: string; until: string; bucket?: string }) =>
    request<{
      goals_per_hour: Array<{ ts: string; count: number; success: number; failed: number }>;
      cost_per_hour: Array<{ ts: string; cost_usd: number }>;
      avg_latency_per_hour: Array<{ ts: string; p50_ms: number; p95_ms: number }>;
    }>(
      `/observability/timeseries?since=${encodeURIComponent(params.since)}&until=${encodeURIComponent(params.until)}&bucket=${params.bucket ?? 'hour'}`
    ),
};

export const logsApi = {
  list: (params?: { limit?: number; level?: string; since?: string }) => {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.level) qs.set('level', params.level);
    if (params?.since) qs.set('since', params.since);
    const q = qs.toString();
    return request<{ logs: LogEntry[]; total: number }>(
      `/observability/logs${q ? `?${q}` : ''}`
    );
  },
};

// ── MFA API ───────────────────────────────────────────────────────────────────

export interface MFAStatus {
  enabled: boolean;
  has_pending_enrollment: boolean;
  recovery_codes_count: number;
}

export interface MFAEnrollResponse {
  secret: string;
  provisioning_uri: string;
  qr_code: string | null;
  account_name: string;
  issuer: string;
  algorithm: string;
  digits: number;
  period: number;
}

export interface MFAVerifyEnrollResponse {
  status: 'enabled';
  recovery_codes: string[];
  message: string;
}

export const mfaApi = {
  status: () => request<MFAStatus>('/auth/mfa/status'),
  enroll: () => request<MFAEnrollResponse>('/auth/mfa/enroll', { method: 'POST' }),
  verifyEnrollment: (code: string) =>
    request<MFAVerifyEnrollResponse>('/auth/mfa/verify-enrollment', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  verify: (code: string) =>
    request<{
      status: string;
      method: string;
      remaining_recovery_codes?: number;
      /** X-MFA-Token for subsequent requests. */
      session_token?: string;
      expires_in?: number;
    }>('/auth/mfa/verify', { method: 'POST', body: JSON.stringify({ code }) }),
  disable: (code: string) =>
    request<{ status: string; message: string }>('/auth/mfa/disable', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  getRecoveryCodes: () =>
    request<{ remaining: number; message: string }>('/auth/mfa/recovery-codes'),
  regenerateCodes: (code: string) =>
    request<{ recovery_codes: string[]; message: string }>('/auth/mfa/regenerate', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
};

// ── Collaboration (CRDT short-lived tokens) ───────────────────────────────────

export const collabApi = {
  /**
   * Generate a short-lived CRDT token for WebSocket authentication.
   * Preferred over sending the long-lived API key in a query parameter.
   * Token TTL matches the backend _CRDT_TOKEN_TTL (default 1 hour).
   */
  getCrdtToken: () =>
    request<{ token: string; expires_in: number }>('/collab/crdt-token', { method: 'POST' }),
};

// ── OCR Document Extraction ────────────────────────────────────────────────────

export type OcrDocumentType =
  | 'pan_card' | 'aadhaar' | 'passport' | 'driving_license' | 'voter_id'
  | 'gstin_certificate' | 'bank_cheque' | 'salary_slip' | 'address_proof'
  | 'invoice' | 'bank_statement' | 'receipt' | 'general';

export interface OcrFieldResult {
  value: string;
  confidence: number;
  is_valid: boolean;
  raw_value?: string | null;
}

export interface OcrResponse {
  raw_text: string;
  document_type: OcrDocumentType;
  fields: Record<string, OcrFieldResult>;
  engine_used: 'tesseract' | 'llm_vision';
  overall_confidence: number;
  page_count: number;
}

export interface BatchOcrResponse {
  results: (OcrResponse | null)[];
  total: number;
  succeeded: number;
  failed: number;
}

export const ocrApi = {
  /** Upload a file (image or PDF) and extract text + structured fields. */
  extractFile: (file: File): Promise<OcrResponse> => {
    const form = new FormData();
    form.append('file', file);
    return request<OcrResponse>('/ocr/extract', { method: 'POST', body: form });
  },

  /** Send pre-encoded base64 image or PDF for extraction. */
  extractBase64: (
    imageBase64: string,
    pdfBase64: string,
    filename = 'document',
  ): Promise<OcrResponse> =>
    request<OcrResponse>('/ocr/extract', {
      method: 'POST',
      body: JSON.stringify({ image_base64: imageBase64, pdf_base64: pdfBase64, filename }),
    }),

  /** Process up to 10 documents concurrently via the batch endpoint. */
  batch: (
    documents: Array<{ image_base64?: string; pdf_base64?: string; filename?: string }>,
  ): Promise<BatchOcrResponse> =>
    request<BatchOcrResponse>('/ocr/batch', {
      method: 'POST',
      body: JSON.stringify({ documents }),
    }),
};

// ── Admin (platform-level) ────────────────────────────────────────────────────
// /admin/* requires a PLATFORM admin (app/tenancy/platform_admin.py): either the
// signed-in tenant is an admin of an operator tenant (PLATFORM_ADMIN_TENANT_IDS;
// any tenant admin outside production while that is unset), or the request
// carries the platform admin key as X-Admin-Key. A refusal is a 403 (not a 401),
// so it never trips the logout-on-401 logic in request().
//
// The admin key, when the operator types one on the Admin page, is held in this
// module's memory only — never in a store, localStorage or sessionStorage — and
// is sent only on /admin requests.
let platformAdminKey: string | null = null;

/** Set (or clear with null/"") the in-memory platform admin key for /admin calls. */
export function setPlatformAdminKey(key: string | null): void {
  platformAdminKey = key && key.trim() ? key.trim() : null;
}

function adminHeaders(): Record<string, string> {
  return platformAdminKey ? { 'X-Admin-Key': platformAdminKey } : {};
}

export interface AdminTenant {
  tenant_id: string;
  name?: string;
  plan: string;
  created_at?: string;
  goal_count?: number;
}

export interface PlatformUsage {
  active_goals: number;
  total_tenants: number;
  goals_today?: number;
  /** Mean duration of goals completed today (UTC); null when none has. */
  avg_latency_ms?: number | null;
  total_goals?: number;
  completed_today?: number;
  goals_by_status?: Record<string, number>;
}

export const adminApi = {
  // GET /admin/tenants supports server-side offset pagination (limit + offset)
  // and returns { tenants, total }. It has NO server-side search param, so
  // AdminPage searches/filters the current page client-side (documented there).
  listTenants: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams({ limit: String(params?.limit ?? 25) });
    if (params?.offset) qs.set('offset', String(params.offset));
    return request<{ tenants: AdminTenant[]; total: number }>(`/admin/tenants?${qs}`, {
      headers: adminHeaders(),
    });
  },
  updatePlan: (tenantId: string, plan: string) =>
    request<unknown>(`/admin/tenants/${tenantId}/plan`, {
      method: 'PUT',
      body: JSON.stringify({ plan }),
      headers: adminHeaders(),
    }),
  getPlatformUsage: () => request<PlatformUsage>('/admin/usage', { headers: adminHeaders() }),
};

// ─────────────────────────────────────────────────────────────────────────────
// Workflow Automation Engine API  (new — separate from legacy workflowsApi)
// ─────────────────────────────────────────────────────────────────────────────

export interface WEWorkflow {
  id: string;
  name: string;
  description: string;
  status: 'draft' | 'published' | 'archived' | 'pending_approval';
  version: string;
  labels: Record<string, string>;
  trigger_type?: string;
  schedule_cron?: string;
  created_at: string;
  updated_at: string;
  /** Caller's per-workflow access level (detail endpoint only). */
  access?: 'viewer' | 'runner' | 'editor' | 'admin' | null;
}

export interface WEWebhookDelivery {
  id: string;
  /** Fingerprint of the webhook token (never the token itself). */
  webhook_token: string;
  status: 'pending' | 'failed' | 'succeeded' | 'dead' | string;
  attempts: number;
  last_error?: string | null;
  run_id?: string | null;
  received_at?: string | null;
  last_attempted_at?: string | null;
  completed_at?: string | null;
}

export interface WEWorkflowPermission {
  id: string;
  subject_type: string;
  subject_id: string;
  permission: 'viewer' | 'runner' | 'editor' | 'admin';
  granted_by?: string | null;
  granted_at?: string | null;
}

export interface WERun {
  run_id: string;
  workflow_id: string;
  workflow_name?: string;
  status: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  error?: string;
  /** The step whose failure stopped the run. */
  error_step_id?: string | null;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  step_count: number;
  cost_usd: number;
  tokens_used?: number;
}

export interface WEWorkflowTemplate {
  slug: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  complexity: string;
  popularity_score: number;
  definition?: Record<string, unknown>;
}

export interface WEApprovalRequest {
  request_id: string;
  run_id: string;
  step_id: string;
  step_name?: string;
  workflow_id: string;
  workflow_name?: string;
  priority: string;
  status: string;
  context: Array<{ display_type: string; title: string; data: unknown }>;
  actions: Array<{ id: string; label: string }>;
  deadline_at: string | null;
  created_at: string;
  note?: string;
  assigned_to?: string | null;
  assigned_role?: string | null;
  /** Whether the current caller may decide it (server-computed). */
  can_decide?: boolean;
  /** Deciding would be an audited admin override. */
  requires_override?: boolean;
}

export interface WEStepResult {
  step_id: string;
  step_type: string;
  status: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
}

const V1 = '/api/v1';

export const workflowEngineApi = {
  // ── Definitions ──────────────────────────────────────────────────────────

  list: (params?: { page?: number; per_page?: number; status?: string; label?: string }) => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set('page', String(params.page));
    if (params?.per_page) qs.set('per_page', String(params.per_page));
    if (params?.status) qs.set('status', params.status);
    if (params?.label) qs.set('label', params.label);
    return request<{ items: WEWorkflow[]; total: number; page: number; per_page: number }>(
      `${V1}/workflows?${qs}`
    );
  },

  get: (id: string) => request<WEWorkflow>(`${V1}/workflows/${id}`),

  create: (body: { name: string; description?: string; definition: Record<string, unknown>; labels?: Record<string, string> }) =>
    request<WEWorkflow>(`${V1}/workflows`, { method: 'POST', body: JSON.stringify(body) }),

  update: (id: string, body: { name?: string; description?: string; definition?: Record<string, unknown>; labels?: Record<string, string> }) =>
    request<WEWorkflow>(`${V1}/workflows/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  delete: (id: string) =>
    request<void>(`${V1}/workflows/${id}`, { method: 'DELETE' }),

  publish: (id: string) =>
    request<WEWorkflow>(`${V1}/workflows/${id}/publish`, { method: 'POST', body: '{}' }),

  unpublish: (id: string) =>
    request<WEWorkflow>(`${V1}/workflows/${id}/unpublish`, { method: 'POST', body: '{}' }),

  /** Four-eyes publish approval (workflows with requires_publish_approval). */
  approvePublish: (id: string, note = '') =>
    request<WEWorkflow>(`${V1}/workflows/${id}/approve-publish`, {
      method: 'POST', body: JSON.stringify({ note }),
    }),

  rejectPublish: (id: string, note = '') =>
    request<WEWorkflow>(`${V1}/workflows/${id}/reject-publish`, {
      method: 'POST', body: JSON.stringify({ note }),
    }),

  validate: (id: string) =>
    request<{ valid: boolean; errors: string[] }>(`${V1}/workflows/${id}/validate`, { method: 'POST', body: '{}' }),

  /** Revoke the webhook URL and issue a new one (old URLs answer 401). */
  rotateWebhook: (id: string) =>
    request<{ workflow_id: string; webhook_path: string; webhook_url: string }>(
      `${V1}/workflows/${id}/webhook/rotate`,
      { method: 'POST', body: '{}' },
    ),

  /** Dead-lettered webhook deliveries (runs that could not be started). */
  listWebhookEvents: (id: string, params?: { page?: number; per_page?: number }) => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set('page', String(params.page));
    if (params?.per_page) qs.set('per_page', String(params.per_page));
    return request<{ items: WEWebhookDelivery[]; total: number; page: number; per_page: number }>(
      `${V1}/workflows/${id}/webhooks?${qs}`,
    );
  },

  // ── Per-workflow access control ───────────────────────────────────────────
  listPermissions: (id: string) =>
    request<WEWorkflowPermission[]>(`${V1}/workflows/${id}/permissions`),

  addPermission: (
    id: string,
    body: { subject: string; role: WEWorkflowPermission['permission']; subject_type?: 'user' | 'role' },
  ) =>
    request<WEWorkflowPermission>(`${V1}/workflows/${id}/permissions`, {
      method: 'POST', body: JSON.stringify(body),
    }),

  removePermission: (id: string, permissionId: string) =>
    request<void>(`${V1}/workflows/${id}/permissions/${permissionId}`, { method: 'DELETE' }),

  /** The real inbound webhook (POST /wf-hooks/{token}); only issued once published. */
  getWebhook: (id: string) =>
    request<{
      workflow_id: string;
      published: boolean;
      webhook_path?: string;
      webhook_url?: string;
      callback_signature_header?: string;
      callback_signing_secret?: string;
    }>(`${V1}/workflows/${id}/webhook`),

  exportYaml: (id: string) => request<string>(`${V1}/workflows/${id}/yaml`),

  clone: (id: string) =>
    request<WEWorkflow>(`${V1}/workflows/${id}/clone`, { method: 'POST', body: '{}' }),

  // ── Runs ─────────────────────────────────────────────────────────────────

  trigger: (id: string, body: { inputs?: Record<string, unknown>; dry_run?: boolean; idempotency_key?: string; callback_url?: string }) =>
    request<WERun>(`${V1}/workflows/${id}/trigger`, { method: 'POST', body: JSON.stringify(body) }),

  listRuns: (params?: { workflow_id?: string; status?: string; page?: number; per_page?: number }) => {
    const qs = new URLSearchParams();
    if (params?.workflow_id) qs.set('workflow_id', params.workflow_id);
    if (params?.status) qs.set('status', params.status);
    if (params?.page) qs.set('page', String(params.page));
    if (params?.per_page) qs.set('per_page', String(params.per_page));
    return request<{ items: WERun[]; total: number; page: number; per_page: number }>(
      `${V1}/runs?${qs}`
    );
  },

  getRun: (runId: string) => request<WERun>(`${V1}/runs/${runId}`),

  getRunSteps: (runId: string) => request<WEStepResult[]>(`${V1}/runs/${runId}/steps`),

  cancelRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`${V1}/runs/${runId}/cancel`, { method: 'POST', body: '{}' }),

  pauseRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`${V1}/runs/${runId}/pause`, { method: 'POST', body: '{}' }),

  resumeRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`${V1}/runs/${runId}/resume`, { method: 'POST', body: '{}' }),

  debugRun: (runId: string) => request<Record<string, unknown>>(`${V1}/runs/${runId}/debug`),

  // ── Versions ─────────────────────────────────────────────────────────────

  listVersions: (id: string) => request<unknown[]>(`${V1}/workflows/${id}/versions`),

  // ── NL trigger preview ────────────────────────────────────────────────────

  nlTriggerPreview: (description: string) =>
    request<{ trigger: Record<string, unknown>; description: string }>(
      `${V1}/workflows/nl-trigger-preview`,
      { method: 'POST', body: JSON.stringify({ description }) }
    ),

  // ── Templates ─────────────────────────────────────────────────────────────

  listTemplates: (params?: { category?: string; q?: string; page?: number; per_page?: number }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set('category', params.category);
    if (params?.q) qs.set('q', params.q);
    if (params?.page) qs.set('page', String(params.page));
    if (params?.per_page) qs.set('per_page', String(params.per_page));
    return request<{ items: WEWorkflowTemplate[]; total: number }>(`${V1}/workflow-templates?${qs}`);
  },

  getTemplate: (slug: string) => request<WEWorkflowTemplate>(`${V1}/workflow-templates/${slug}`),

  forkTemplate: (slug: string, overrides?: Record<string, unknown>) =>
    request<WEWorkflow>(`${V1}/workflow-templates/${slug}/fork`, {
      method: 'POST', body: JSON.stringify(overrides ?? {}),
    }),

  listTemplateCategories: () => request<Array<{ category: string; count: number }>>(
    `${V1}/workflow-templates/categories`
  ),

  // ── HITL / Approvals ──────────────────────────────────────────────────────

  listApprovals: (params?: { priority?: string; page?: number; per_page?: number }) => {
    const qs = new URLSearchParams();
    if (params?.priority) qs.set('priority', params.priority);
    if (params?.page) qs.set('page', String(params.page));
    if (params?.per_page) qs.set('per_page', String(params.per_page));
    return request<{ items: WEApprovalRequest[]; total: number }>(`${V1}/approvals?${qs}`);
  },

  decideApproval: (requestId: string, body: { action: string; note?: string; form_data?: Record<string, unknown> }) =>
    request<unknown>(`${V1}/approvals/${requestId}/decide`, { method: 'POST', body: JSON.stringify(body) }),

  delegateApproval: (requestId: string, toUserId: string, note?: string) =>
    request<unknown>(`${V1}/approvals/${requestId}/delegate`, {
      method: 'POST', body: JSON.stringify({ to_user_id: toUserId, note: note ?? '' }),
    }),

  approvalStats: () => request<{ pending_count: number; total_requests: number; avg_resolution_seconds: number }>(
    `${V1}/approvals/stats`
  ),

  // ── Analytics ─────────────────────────────────────────────────────────────

  analyticsSummary: (days = 7) =>
    request<Record<string, unknown>>(`${V1}/workflows/analytics/summary?days=${days}`),

  workflowAnalytics: (id: string, days = 7) =>
    request<Record<string, unknown>>(`${V1}/workflows/${id}/analytics?days=${days}`),
};
