/**
 * useOrgNeuralState — converts org SSE events to visual constellation state.
 * Spec §3.3: bridges OrgRealtimeManager events to AgentConstellation props.
 */
import { useCallback, useReducer, useRef } from 'react';
import { useOrgHealth } from '../hooks/useOrg';
import type { OrgEvent } from '../OrgRealtimeManager';
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
  /** Feed a live org SSE event into the constellation state. Wire this to
   *  useOrgRealtimeManager's onEvent so agents spawn/animate as events arrive. */
  applyEvent:         (event: OrgEvent) => void;
}

type ReducerState = Omit<OrgNeuralState, 'applyEvent'>;

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

  const [state, dispatch] = useReducer(
    (s: ReducerState, action: { type: string; payload: Record<string, unknown> }): ReducerState => {
      const agentState = eventTypeToAgentState(action.type);
      if (agentState !== null) {
        const agentId = String(action.payload.agent_id ?? action.payload.entity_id ?? '');
        if (!agentId) return s;
        const role = action.payload.role ? String(action.payload.role) : undefined;
        const missionId = action.payload.mission_id ? String(action.payload.mission_id) : undefined;
        const label = role || agentId.replace(/^agent-/, 'Agent ');
        const exists = s.agents.find(a => a.id === agentId);
        const updated = exists
          ? s.agents.map(a => a.id === agentId ? { ...a, state: agentState, role, missionId } : a)
          : [...s.agents, { id: agentId, label, role, missionId, state: agentState, goalCount: 1 }];
        // Cap the constellation so a long-running org stays legible.
        return { ...s, agents: updated.slice(-24) };
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

  const applyEvent = useCallback((event: OrgEvent) => {
    dispatch({ type: event.event_type, payload: event.payload });
  }, []);

  const result: OrgNeuralState = {
    ...state,
    activeMissions:   (health as any)?.active_missions  ?? state.activeMissions,
    pendingApprovals: (health as any)?.pending_approvals ?? state.pendingApprovals,
    overallHealth:    (health as any)?.overall_health    ?? state.overallHealth,
    applyEvent,
  };

  return result;
}
