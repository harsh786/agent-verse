/**
 * useAgentNeuralGraph — builds live agent constellation graph from org SSE events.
 * Spec §3.3: Converts org.agent.* and org.mission.* events to graph state.
 */
import { useReducer, useEffect, useRef } from 'react';
import type { AgentNeuralState } from '@/components/neural/AgentNeuralNode';

export interface NeuralAgentNode {
  id:         string;
  label:      string;
  role?:      string;
  state:      AgentNeuralState;
  missionId?: string;
  deptId?:    string;
  goalCount:  number;
}

export interface NeuralEdge {
  id:     string;
  source: string;
  target: string;
  type:   'delegation' | 'collaboration' | 'debate' | 'supervision';
  active: boolean;
}

export interface AgentNeuralGraph {
  agents: Map<string, NeuralAgentNode>;
  edges:  NeuralEdge[];
  communicatingPairs: [string, string][];
}

type OrgEventLike = {
  event_type?: string;
  payload?:    Record<string, unknown>;
  org_id?:     string;
};

function initialGraph(): AgentNeuralGraph {
  return { agents: new Map(), edges: [], communicatingPairs: [] };
}

function orgEventToState(eventType: string): AgentNeuralState {
  if (eventType.includes('activated'))     return 'executing';
  if (eventType.includes('idle'))          return 'idle';
  if (eventType.includes('blocked'))       return 'blocked';
  if (eventType.includes('escalated'))     return 'waiting';
  if (eventType.includes('completed'))     return 'completed';
  if (eventType.includes('failed'))        return 'error';
  if (eventType.includes('forming'))       return 'thinking';
  if (eventType.includes('formed'))        return 'communicating';
  return 'idle';
}

export function useAgentNeuralGraph(orgEvents: OrgEventLike[]): AgentNeuralGraph {
  const [graph, dispatch] = useReducer(
    (state: AgentNeuralGraph, event: OrgEventLike): AgentNeuralGraph => {
      const et      = event.event_type ?? '';
      const payload = event.payload ?? {};

      // Agent state changes
      if (et.startsWith('org.agent.')) {
        const agentId = String(payload.agent_id ?? payload.entity_id ?? '');
        if (!agentId) return state;
        const agentState = orgEventToState(et);
        const agents = new Map(state.agents);
        const existing = agents.get(agentId) ?? {
          id: agentId, label: String(payload.agent_name ?? agentId.slice(-8)),
          state: 'idle' as AgentNeuralState, goalCount: 0,
        };
        agents.set(agentId, { ...existing, state: agentState });
        return { ...state, agents };
      }

      // Mission creates team → agents as communicating pair
      if (et === 'org.mission.started' || et === 'org.team.formed') {
        const teamAgents = (payload.agent_ids as string[] | undefined) ?? [];
        if (teamAgents.length >= 2) {
          const newPairs: [string, string][] = [[teamAgents[0], teamAgents[1]]];
          return { ...state, communicatingPairs: [...state.communicatingPairs, ...newPairs].slice(-5) };
        }
        return state;
      }

      // Mission complete → clear comms
      if (et === 'org.mission.completed' || et === 'org.mission.failed') {
        return { ...state, communicatingPairs: [] };
      }

      return state;
    },
    undefined,
    initialGraph,
  );

  const lastLen = useRef(0);

  useEffect(() => {
    const newEvts = orgEvents.slice(lastLen.current);
    lastLen.current = orgEvents.length;
    for (const evt of newEvts) {
      dispatch(evt);
    }
  }, [orgEvents]);

  return graph;
}
