import type { Agent, Connector, Goal, GoalEvent, SubmitGoalOptions } from './types.js';
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
    { timeout = 300, pollInterval = 2 }: { timeout?: number; pollInterval?: number } = {},
  ): Promise<Goal> {
    const deadline = Date.now() + timeout * 1000;
    while (Date.now() < deadline) {
      const goal = await this.getGoal(goalId);
      if (goal.status === 'complete') return goal;
      if (goal.status === 'failed' || goal.status === 'cancelled') {
        throw new GoalFailedError(goalId, `Goal ${goal.status}`);
      }
      await new Promise(r => setTimeout(r, pollInterval * 1000));
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

  async listAgents(): Promise<Agent[]> {
    const data = await this.request<Agent[] | { agents: Agent[] }>('GET', '/agents');
    return Array.isArray(data) ? data : (data as { agents: Agent[] }).agents ?? [];
  }

  async deleteAgent(agentId: string): Promise<void> {
    await this.request('DELETE', `/agents/${agentId}`);
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
}
