/**
 * useOrgNeuralState — converts org SSE events to visual constellation state.
 * Spec §3.3: bridges OrgRealtimeManager events to AgentConstellation props.
 */
import { useReducer, useRef } from 'react';
import { useOrgHealth } from '../hooks/useOrg';
import type { AgentNeuralState } from '@/components/neural/AgentNeuralNode';

export interface OrgNeuralAgent {
  id:         string;
  label:      string;
  role?:      string;
  deptId?:    string;
  state:      AgentNeuralState;
  goalCount:  number;
  missionId?: string;
}

export interface OrgNeuralState {
  agents:             OrgNeuralAgent[];
  activeMissions:     number;
  pendingApprovals:   number;
  overallHealth:      string;
  communicatingPairs: [string, string][];
}

function eventTypeToAgentState(eventType: string): AgentNeuralState | null {
  if (eventType === 'org.agent.activated')     return 'executing';
  if (eventType === 'org.agent.idle')          return 'idle';
  if (eventType === 'org.agent.blocked')       return 'blocked';
  if (eventType === 'org.agent.escalated')     return 'waiting';
  if (eventType === 'org.agent.completed_task') return 'completed';
  if (eventType === 'org.agent.failed_task')   return 'error';
  return null;
}

export function useOrgNeuralState(orgId: string | null): OrgNeuralState {
  const { data: health } = useOrgHealth(orgId);

  const [state] = useReducer(
    (s: OrgNeuralState, action: { type: string; payload: Record<string, unknown> }): OrgNeuralState => {
      const agentState = eventTypeToAgentState(action.type);
      if (agentState !== null) {
        const agentId = String(action.payload.agent_id ?? action.payload.entity_id ?? '');
        if (!agentId) return s;
        const exists = s.agents.find(a => a.id === agentId);
        const updated = exists
          ? s.agents.map(a => a.id === agentId ? { ...a, state: agentState } : a)
          : [...s.agents, { id: agentId, label: agentId.slice(-8), state: agentState, goalCount: 0 }];
        return { ...s, agents: updated };
      }
      if (action.type === 'org.team.formed') {
        const ids = (action.payload.agent_ids as string[] | undefined) ?? [];
        if (ids.length >= 2) {
          const pair: [string, string] = [ids[0], ids[1]];
          return { ...s, communicatingPairs: [...s.communicatingPairs, pair].slice(-5) };
        }
      }
      if (action.type === 'org.mission.completed' || action.type === 'org.mission.failed') {
        return { ...s, communicatingPairs: [] };
      }
      return s;
    },
    {
      agents: [], activeMissions: 0, pendingApprovals: 0,
      overallHealth: 'healthy', communicatingPairs: [],
    },
  );

  // Sync health data
  const healthRef = useRef(health);
  healthRef.current = health;

  const result: OrgNeuralState = {
    ...state,
    activeMissions:   (health as any)?.active_missions  ?? state.activeMissions,
    pendingApprovals: (health as any)?.pending_approvals ?? state.pendingApprovals,
    overallHealth:    (health as any)?.overall_health    ?? state.overallHealth,
  };

  return result;
}
