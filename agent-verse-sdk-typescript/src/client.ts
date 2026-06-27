import type {
  Agent,
  AgentSnapshot,
  Connector,
  ConnectorSpec,
  ConnectorTestResult,
  ConsentRecord,
  CostMetrics,
  CreateAgentRequest,
  CreateScheduleRequest,
  EvalScorecard,
  GdprExportJob,
  Goal,
  GoalEvent,
  GoalMetrics,
  GoldenTask,
  Memory,
  RolloutGateResult,
  Schedule,
  SearchResult,
  SubmitGoalOptions,
  ToolReliabilityStats,
  UpdateAgentRequest,
} from './types.js';
import { AgentVerseError, AuthError, GoalFailedError, GoalTimeoutError, NotFoundError } from './errors.js';

export class AgentVerseClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;

  constructor(apiKey: string, baseUrl = 'http://localhost:8000') {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private get headers(): HeadersInit {
    return {
      'X-API-Key': this.apiKey,
      'Content-Type': 'application/json',
    };
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      method,
      headers: this.headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (res.status === 401) throw new AuthError();
    if (res.status === 404) throw new NotFoundError(path);
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new AgentVerseError(`API error ${res.status}: ${text.slice(0, 200)}`, res.status);
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  }

  // ── Goals ────────────────────────────────────────────────────────────────

  async submitGoal(goal: string, options: SubmitGoalOptions = {}): Promise<Goal> {
    return this.request<Goal>('POST', '/goals', {
      goal,
      priority: options.priority ?? 'normal',
      dry_run: options.dry_run ?? false,
      agent_id: options.agent_id,
      persistence_mode: options.persistence_mode ?? false,
      workflow_mode: options.workflow_mode ?? 'single_agent',
    });
  }

  async getGoal(goalId: string): Promise<Goal> {
    return this.request<Goal>('GET', `/goals/${goalId}`);
  }

  async listGoals(): Promise<Goal[]> {
    const data = await this.request<{ goals: Goal[] }>('GET', '/goals');
    return data.goals ?? [];
  }

  async cancelGoal(goalId: string): Promise<void> {
    await this.request('POST', `/goals/${goalId}/cancel`);
  }

  async waitForGoal(
    goalId: string,
    options?: { timeout?: number; pollInterval?: number },
  ): Promise<Goal> {
    const timeout = options?.timeout ?? 300;

    // Try SSE streaming first (more efficient)
    try {
      let finalGoal: Goal | null = null;
      const deadline = Date.now() + timeout * 1000;

      for await (const event of this.streamGoal(goalId)) {
        if (Date.now() > deadline) break;

        const eventType = (event as any).type ?? '';
        if (eventType === 'goal_complete' || eventType === 'goal_finished') {
          finalGoal = await this.getGoal(goalId);
          return finalGoal;
        }
        if (eventType === 'goal_failed' || eventType === 'goal_error') {
          finalGoal = await this.getGoal(goalId);
          const reason = (event.payload as any)?.reason ?? 'Goal failed';
          throw new GoalFailedError(goalId, `Goal ${goalId} failed: ${reason}`);
        }
      }

      return await this.getGoal(goalId);
    } catch (err) {
      // SSE failed or timed out — fall back to polling
      if (err instanceof GoalFailedError) {
        throw err;  // Re-throw actual goal failures
      }
      if (err instanceof GoalTimeoutError) {
        throw err;
      }
    }

    // Polling fallback
    const pollInterval = options?.pollInterval ?? 2000;
    const deadline = Date.now() + timeout * 1000;
    while (Date.now() < deadline) {
      const goal = await this.getGoal(goalId);
      const status = goal.status ?? '';
      if (['complete', 'completed', 'failed', 'error', 'cancelled'].includes(status)) {
        if (status === 'failed' || status === 'error') {
          throw new GoalFailedError(goalId, `Goal ${goalId} failed: ${(goal as any).error_message ?? 'unknown error'}`);
        }
        return goal;
      }
      await new Promise(resolve => setTimeout(resolve, pollInterval));
    }

    throw new GoalTimeoutError(goalId, timeout);
  }

  async *streamGoal(goalId: string): AsyncGenerator<GoalEvent> {
    const url = `${this.baseUrl}/goals/${goalId}/stream`;
    const res = await fetch(url, { headers: this.headers });
    if (!res.ok || !res.body) throw new AgentVerseError(`Stream failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        for (const line of frame.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              yield JSON.parse(line.slice(6)) as GoalEvent;
            } catch {
              // skip malformed
            }
          }
        }
      }
    }
  }

  // ── Agents ───────────────────────────────────────────────────────────────

  async createAgent(name: string, goalTemplate = '', extra: Record<string, unknown> = {}): Promise<Agent> {
    return this.request<Agent>('POST', '/agents', { name, goal_template: goalTemplate, ...extra });
  }

  async getAgent(agentId: string): Promise<Agent> {
    return this.request<Agent>('GET', `/agents/${agentId}`);
  }

  async updateAgent(agentId: string, data: UpdateAgentRequest): Promise<Agent> {
    return this.request<Agent>('PUT', `/agents/${agentId}`, data);
  }

  async runAgent(agentId: string, goal: string, options?: {
    dryRun?: boolean;
    autonomyMode?: string;
  }): Promise<Goal> {
    return this.submitGoal(goal, {
      agent_id: agentId,
      dry_run: options?.dryRun,
    });
  }

  async listAgents(): Promise<Agent[]> {
    const data = await this.request<Agent[] | { agents: Agent[] }>('GET', '/agents');
    return Array.isArray(data) ? data : (data as { agents: Agent[] }).agents ?? [];
  }

  async deleteAgent(agentId: string): Promise<void> {
    await this.request('DELETE', `/agents/${agentId}`);
  }

  async snapshotAgent(agentId: string): Promise<AgentSnapshot> {
    return this.request<AgentSnapshot>('POST', `/agents/${agentId}/snapshot`);
  }

  async listAgentVersions(agentId: string): Promise<AgentSnapshot[]> {
    return this.request<AgentSnapshot[]>('GET', `/agents/${agentId}/versions`);
  }

  async rollbackAgent(agentId: string, snapshotId: string): Promise<Agent> {
    return this.request<Agent>('POST', `/agents/${agentId}/rollback/${snapshotId}`);
  }

  // ── Connectors ───────────────────────────────────────────────────────────

  async listConnectors(): Promise<Connector[]> {
    const data = await this.request<Connector[]>('GET', '/connectors');
    return Array.isArray(data) ? data : [];
  }

  async registerConnector(
    name: string, url: string, authType = 'bearer',
    authConfig: Record<string, string> = {},
  ): Promise<Connector> {
    return this.request<Connector>('POST', '/connectors', {
      name, url, auth_type: authType, auth_config: authConfig,
    });
  }

  async deleteConnector(serverId: string): Promise<void> {
    await this.request('DELETE', `/connectors/${serverId}`);
  }

  async testConnector(serverId: string): Promise<ConnectorTestResult> {
    return this.request<ConnectorTestResult>('POST', `/connectors/${serverId}/test`);
  }

  async getConnectorCatalog(): Promise<ConnectorSpec[]> {
    return this.request<ConnectorSpec[]>('GET', '/connectors/catalog');
  }

  // ── Schedules ────────────────────────────────────────────────────────────

  async listSchedules(): Promise<Schedule[]> {
    return this.request<Schedule[]>('GET', '/schedules');
  }

  async createSchedule(data: CreateScheduleRequest): Promise<Schedule> {
    return this.request<Schedule>('POST', '/schedules', data);
  }

  async deleteSchedule(scheduleId: string): Promise<void> {
    await this.request('DELETE', `/schedules/${scheduleId}`);
  }

  async createScheduleNl(command: string): Promise<Schedule> {
    return this.request<Schedule>('POST', '/schedules/nl', { command });
  }

  // ── Memory ───────────────────────────────────────────────────────────────

  async recallMemory(query: string, limit = 10): Promise<Memory[]> {
    return this.request<Memory[]>('GET', `/memory/recall?q=${encodeURIComponent(query)}&limit=${limit}`);
  }

  async storeMemory(content: string, tags?: string[]): Promise<Memory> {
    return this.request<Memory>('POST', '/memory', { content, tags });
  }

  // ── Knowledge ────────────────────────────────────────────────────────────

  async searchKnowledge(collectionId: string, query: string, limit = 10): Promise<SearchResult[]> {
    return this.request<SearchResult[]>(
      'GET',
      `/knowledge/search?collection_id=${collectionId}&q=${encodeURIComponent(query)}&limit=${limit}`,
    );
  }

  // ── Analytics ────────────────────────────────────────────────────────────

  async getGoalMetrics(days = 30): Promise<GoalMetrics> {
    return this.request<GoalMetrics>('GET', `/analytics/goals?days=${days}`);
  }

  async getCostMetrics(days = 30): Promise<CostMetrics> {
    return this.request<CostMetrics>('GET', `/analytics/cost?days=${days}`);
  }

  // ── Tool reliability ──────────────────────────────────────────────────────

  async getToolReliability(): Promise<ToolReliabilityStats[]> {
    return this.request<ToolReliabilityStats[]>('GET', '/memory/tool-reliability');
  }

  // ── Agent rollout gate ────────────────────────────────────────────────────

  async checkRolloutGate(agentId: string, evalSuiteId?: string): Promise<RolloutGateResult> {
    const params = evalSuiteId ? `?eval_suite_id=${evalSuiteId}` : '';
    return this.request<RolloutGateResult>('GET', `/agents/${agentId}/rollout-gate${params}`);
  }

  // ── Consent management ────────────────────────────────────────────────────

  async recordConsent(purpose: string, legalBasis?: string): Promise<ConsentRecord> {
    return this.request<ConsentRecord>('POST', '/compliance/consent', {
      purpose,
      legal_basis: legalBasis ?? 'legitimate_interest',
    });
  }

  // ── Async GDPR export ─────────────────────────────────────────────────────

  async startGdprExport(): Promise<GdprExportJob> {
    return this.request<GdprExportJob>('POST', '/compliance/export/start');
  }

  async getGdprExportStatus(jobId: string): Promise<GdprExportJob> {
    return this.request<GdprExportJob>('GET', `/compliance/export/jobs/${jobId}`);
  }

  // ── Golden tasks ──────────────────────────────────────────────────────────

  async listGoldenTasks(evalSuiteId: string): Promise<GoldenTask[]> {
    return this.request<GoldenTask[]>('GET', `/eval/golden-tasks?eval_suite_id=${evalSuiteId}`);
  }

  // ── Goal evaluation ───────────────────────────────────────────────────────

  async getGoalEvaluation(goalId: string): Promise<EvalScorecard> {
    return this.request<EvalScorecard>('GET', `/goals/${goalId}/evaluation`);
  }
}