/**
 * Civilization API client — typed wrapper for all /civilizations endpoints.
 */
import { apiFetch } from './client';

export interface CivilizationConstitution {
  max_depth?: number;
  max_total_agents?: number;
  max_concurrent_agents?: number;
  total_budget_usd?: number;
  per_agent_budget_usd?: number;
  budget_decay?: number;
  spawn_rate_limit_per_min?: number;
  high_risk_requires_hitl?: boolean;
  inherited_policy_ids?: string[];
  autonomy_ceiling?: string;
  reputation_floor?: number;
  idle_ttl_seconds?: number;
}

export interface Civilization {
  id: string;
  name: string;
  status: 'active' | 'paused' | 'retired';
  constitution: CivilizationConstitution;
  created_at: string;
  metrics?: CivilizationMetrics;
}

export interface CivilizationMetrics {
  total_members: number;
  active_members: number;
  idle_members: number;
  retired_members: number;
  total_budget_spent_usd: number;
  avg_reputation: number;
  max_reputation: number;
  min_reputation: number;
}

export interface SocietyNode {
  id: string;
  label: string;
  status: string;
  reputation: number;
  depth: number;
  budget_spent_usd: number;
}

export interface SocietyEdge {
  source: string;
  target: string;
  type: string;
  topic?: string;
  ts?: string;
}

export interface SpawnRequest {
  id: string;
  requester_agent_id: string;
  requested_capability: string;
  decision: 'approved' | 'denied';
  reason: string;
  created_at: string;
}

export interface BlackboardEntry {
  id: string;
  author_agent_id: string;
  topic: string;
  content: string;
  confidence: number;
  version: number;
  created_at: string;
}

export interface LearningRecord {
  id: string;
  candidate: string;
  source_agent_id: string;
  status: 'candidate' | 'validated' | 'promoted' | 'rejected';
  eval_score: number | null;
  promoted_memory_id: string | null;
  created_at: string;
  decided_at: string | null;
}

export interface CivilizationEvent {
  id: string;
  civilization_id: string;
  type: string;
  payload: Record<string, unknown>;
  ts: string;
}

const BASE = '/civilizations';

export const civilizationApi = {
  list: () => apiFetch<Civilization[]>(BASE),
  create: (name: string, constitution?: CivilizationConstitution) =>
    apiFetch<Civilization>(BASE, { method: 'POST', body: JSON.stringify({ name, constitution: constitution ?? {} }) }),
  get: (id: string) => apiFetch<Civilization>(`${BASE}/${id}`),
  updateConstitution: (id: string, constitution: CivilizationConstitution) =>
    apiFetch<{ updated: boolean }>(`${BASE}/${id}/constitution`, { method: 'PUT', body: JSON.stringify({ constitution }) }),
  submitGoal: (id: string, goal: string, priority?: string) =>
    apiFetch<{ status: string; goal_id?: string; agent_id?: string }>(`${BASE}/${id}/goals`, {
      method: 'POST', body: JSON.stringify({ goal, priority: priority ?? 'normal' }),
    }),
  getGraph: (id: string) => apiFetch<{ nodes: SocietyNode[]; edges: SocietyEdge[] }>(`${BASE}/${id}/graph`),
  getAgentInspector: (civId: string, agentId: string) =>
    apiFetch<Record<string, unknown>>(`${BASE}/${civId}/agents/${agentId}`),
  getBlackboard: (id: string, topic?: string) =>
    apiFetch<BlackboardEntry[]>(`${BASE}/${id}/blackboard${topic ? `?topic=${encodeURIComponent(topic)}` : ''}`),
  getDebates: (id: string) => apiFetch<Record<string, unknown>[]>(`${BASE}/${id}/debates`),
  getLearnings: (id: string, status?: string) =>
    apiFetch<LearningRecord[]>(`${BASE}/${id}/learnings${status ? `?learning_status=${status}` : ''}`),
  getSpawnAudit: (id: string) => apiFetch<SpawnRequest[]>(`${BASE}/${id}/spawns`),
  getReplay: (id: string, since?: string) =>
    apiFetch<{ events: CivilizationEvent[]; count: number }>(`${BASE}/${id}/replay${since ? `?since=${since}` : ''}`),
  control: (id: string, action: string, params?: Record<string, unknown>) =>
    apiFetch<Record<string, unknown>>(`${BASE}/${id}/controls/${action}`, {
      method: 'POST', body: JSON.stringify({ action, params: params ?? {} }),
    }),
  killAgent: (civId: string, agentId: string) =>
    apiFetch<{ killed: string }>(`${BASE}/${civId}/agents/${agentId}/kill`, { method: 'POST' }),
};
