/**
 * WorkflowClient — TypeScript/JavaScript SDK for the Workflow Automation Engine.
 *
 * Usage:
 *   const wf = new WorkflowClient({ apiKey: 'av-...' });
 *   const run = await wf.run('wf-123', { inputs: { email: 'test@example.com' } });
 *   const result = await wf.waitForRun(run.runId, { timeout: 120_000 });
 */

export interface WorkflowRunOptions {
  inputs?: Record<string, unknown>;
  idempotencyKey?: string;
  dryRun?: boolean;
  callbackUrl?: string;
}

export interface WorkflowRun {
  runId: string;
  workflowId: string;
  status: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  error?: string;
  costUsd: number;
  startedAt?: string;
  finishedAt?: string;
}

export interface WorkflowDefinition {
  id: string;
  name: string;
  status: string;
  version: string;
  labels: Record<string, string>;
  triggerType?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ListRunsOptions {
  workflowId?: string;
  status?: string;
  page?: number;
  perPage?: number;
}

export interface WorkflowListOptions {
  page?: number;
  perPage?: number;
  status?: string;
}

export interface WaitForRunOptions {
  timeout?: number;      // ms, default 300_000 (5 min)
  pollInterval?: number; // ms, default 2_000 (2s)
}

export interface WorkflowClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
}

const TERMINAL_STATUSES = new Set(['complete', 'failed', 'cancelled', 'timed_out']);
const DEFAULT_POLL_INTERVAL = 2_000;
const DEFAULT_TIMEOUT = 300_000;

export class WorkflowClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeout: number;

  constructor({ apiKey, baseUrl = 'http://localhost:8000', timeout = 30_000 }: WorkflowClientOptions) {
    if (!apiKey) throw new Error('apiKey is required');
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.timeout = timeout;
  }

  // ── Workflow definitions ──────────────────────────────────────────────────

  async create(
    name: string,
    definition: Record<string, unknown>,
    extras?: Record<string, unknown>,
  ): Promise<WorkflowDefinition> {
    const data = await this._post('/api/v1/workflows', { name, definition, ...extras });
    return this._toDefinition(data);
  }

  async get(workflowId: string): Promise<WorkflowDefinition> {
    const data = await this._get(`/api/v1/workflows/${workflowId}`);
    return this._toDefinition(data);
  }

  async list(opts: WorkflowListOptions = {}): Promise<{ items: WorkflowDefinition[]; total: number }> {
    const params = new URLSearchParams();
    if (opts.page) params.set('page', String(opts.page));
    if (opts.perPage) params.set('per_page', String(opts.perPage));
    if (opts.status) params.set('status', opts.status);
    const data = await this._get(`/api/v1/workflows?${params}`);
    return {
      items: (data.items ?? []).map(this._toDefinition),
      total: data.total ?? 0,
    };
  }

  async publish(workflowId: string): Promise<WorkflowDefinition> {
    const data = await this._post(`/api/v1/workflows/${workflowId}/publish`, {});
    return this._toDefinition(data);
  }

  async unpublish(workflowId: string): Promise<WorkflowDefinition> {
    const data = await this._post(`/api/v1/workflows/${workflowId}/unpublish`, {});
    return this._toDefinition(data);
  }

  // ── Execution ─────────────────────────────────────────────────────────────

  async run(workflowId: string, opts: WorkflowRunOptions = {}): Promise<WorkflowRun> {
    const payload: Record<string, unknown> = { inputs: opts.inputs ?? {} };
    if (opts.idempotencyKey) payload.idempotency_key = opts.idempotencyKey;
    if (opts.dryRun) payload.dry_run = true;
    if (opts.callbackUrl) payload.callback_url = opts.callbackUrl;
    const data = await this._post(`/api/v1/workflows/${workflowId}/trigger`, payload);
    return this._toRun(data);
  }

  async getRun(runId: string): Promise<WorkflowRun> {
    const data = await this._get(`/api/v1/runs/${runId}`);
    return this._toRun(data);
  }

  async listRuns(opts: ListRunsOptions = {}): Promise<{ items: WorkflowRun[]; total: number }> {
    const params = new URLSearchParams();
    if (opts.workflowId) params.set('workflow_id', opts.workflowId);
    if (opts.status) params.set('status', opts.status);
    if (opts.page) params.set('page', String(opts.page));
    if (opts.perPage) params.set('per_page', String(opts.perPage));
    const data = await this._get(`/api/v1/runs?${params}`);
    return {
      items: (data.items ?? []).map(this._toRun),
      total: data.total ?? 0,
    };
  }

  async cancelRun(runId: string): Promise<{ runId: string; status: string }> {
    return this._post(`/api/v1/runs/${runId}/cancel`, {});
  }

  async pauseRun(runId: string): Promise<{ runId: string; status: string }> {
    return this._post(`/api/v1/runs/${runId}/pause`, {});
  }

  async resumeRun(runId: string): Promise<{ runId: string; status: string }> {
    return this._post(`/api/v1/runs/${runId}/resume`, {});
  }

  async waitForRun(runId: string, opts: WaitForRunOptions = {}): Promise<WorkflowRun> {
    const timeout = opts.timeout ?? DEFAULT_TIMEOUT;
    const pollInterval = opts.pollInterval ?? DEFAULT_POLL_INTERVAL;
    const deadline = Date.now() + timeout;

    while (true) {
      const run = await this.getRun(runId);
      if (TERMINAL_STATUSES.has(run.status)) return run;
      const remaining = deadline - Date.now();
      if (remaining <= 0) {
        throw new Error(`Workflow run '${runId}' did not complete within ${timeout}ms`);
      }
      await sleep(Math.min(pollInterval, remaining));
    }
  }

  async *streamRun(runId: string): AsyncGenerator<Record<string, unknown>> {
    const url = `${this.baseUrl}/api/v1/runs/${runId}/stream`;
    const resp = await fetch(url, {
      headers: { 'X-API-Key': this.apiKey, Accept: 'text/event-stream' },
    });
    if (!resp.ok) throw new Error(`Stream failed: ${resp.status}`);
    if (!resp.body) throw new Error('No response body');

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const raw = line.slice(6);
          if (raw === '[DONE]') return;
          try {
            yield JSON.parse(raw);
          } catch {
            // Skip malformed SSE frames
          }
        }
      }
    }
  }

  // ── HITL approvals ────────────────────────────────────────────────────────

  async listApprovals(opts: { page?: number; perPage?: number } = {}): Promise<Record<string, unknown>> {
    const params = new URLSearchParams();
    if (opts.page) params.set('page', String(opts.page));
    if (opts.perPage) params.set('per_page', String(opts.perPage));
    return this._get(`/api/v1/approvals?${params}`);
  }

  async decideApproval(requestId: string, action: string, note = ''): Promise<Record<string, unknown>> {
    return this._post(`/api/v1/approvals/${requestId}/decide`, { action, note });
  }

  // ── Templates ─────────────────────────────────────────────────────────────

  async listTemplates(opts: { category?: string; q?: string } = {}): Promise<Record<string, unknown>> {
    const params = new URLSearchParams();
    if (opts.category) params.set('category', opts.category);
    if (opts.q) params.set('q', opts.q);
    return this._get(`/api/v1/workflow-templates?${params}`);
  }

  async forkTemplate(slug: string, name?: string): Promise<WorkflowDefinition> {
    const data = await this._post(`/api/v1/workflow-templates/${slug}/fork`, name ? { name } : {});
    return this._toDefinition(data);
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  private async _get(path: string): Promise<Record<string, unknown>> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      headers: { 'X-API-Key': this.apiKey },
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`GET ${path} failed: ${resp.status}`);
    return resp.json();
  }

  private async _post(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method: 'POST',
      headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`POST ${path} failed: ${resp.status}`);
    return resp.json();
  }

  private _toRun(data: Record<string, unknown>): WorkflowRun {
    return {
      runId: String(data.run_id ?? ''),
      workflowId: String(data.workflow_id ?? ''),
      status: String(data.status ?? 'pending'),
      inputs: (data.inputs ?? {}) as Record<string, unknown>,
      outputs: (data.outputs ?? {}) as Record<string, unknown>,
      error: data.error as string | undefined,
      costUsd: Number(data.cost_usd ?? 0),
      startedAt: data.started_at as string | undefined,
      finishedAt: data.finished_at as string | undefined,
    };
  }

  private _toDefinition(data: Record<string, unknown>): WorkflowDefinition {
    return {
      id: String(data.id ?? ''),
      name: String(data.name ?? ''),
      status: String(data.status ?? 'draft'),
      version: String(data.version ?? '1'),
      labels: (data.labels ?? {}) as Record<string, string>,
      triggerType: data.trigger_type as string | undefined,
      createdAt: String(data.created_at ?? ''),
      updatedAt: String(data.updated_at ?? ''),
    };
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
