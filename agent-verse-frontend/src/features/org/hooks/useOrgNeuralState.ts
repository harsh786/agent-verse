/**
 * useOrgNeuralState — converts org SSE events to visual constellation state.
 * Spec §3.3: bridges OrgRealtimeManager events to AgentConstellation props.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';
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

/** One recent collaboration message, newest-first, capped (see RECENT_MESSAGES_CAP). */
export interface OrgRecentMessage {
  id:   string;
  from: string;
  to:   string;
  kind: string;
  at:   number;
}

export interface OrgNeuralState {
  agents:             OrgNeuralAgent[];
  activeMissions:     number;
  pendingApprovals:   number;
  overallHealth:      string;
  communicatingPairs: [string, string][];
  /** Capped, newest-first buffer of real collaboration messages — lets a
   *  consumer (e.g. AgentConstellation) color/label a beam by the most
   *  recent message that produced it. */
  recentMessages:     OrgRecentMessage[];
  /** Feed a live org SSE event into the constellation state. Wire this to
   *  useOrgRealtimeManager's onEvent so agents spawn/animate as events arrive. */
  applyEvent:         (event: OrgEvent) => void;
}

/** How long a message-driven comm beam stays lit after its message arrives. */
export const MESSAGE_BEAM_DECAY_MS = 6_000;
const RECENT_MESSAGES_CAP = 30;
const MESSAGE_PAIRS_CAP = 20;

interface MessagePair {
  pair:      [string, string];
  expiresAt: number;
}

interface InternalState {
  agents:           OrgNeuralAgent[];
  activeMissions:   number;
  pendingApprovals: number;
  overallHealth:    string;
  /** Pairs from org.team.formed — persist until the mission completes/fails. */
  teamPairs:        [string, string][];
  /** Pairs from real collaboration messages — transient, expire on their own. */
  messagePairs:     MessagePair[];
  recentMessages:   OrgRecentMessage[];
}

interface Action {
  type:          string;
  payload:       Record<string, unknown>;
  /** Dispatch-time clock reading (Date.now()), captured by applyEvent so the
   *  reducer stays pure and tests can drive it deterministically via a mocked
   *  clock instead of real timers. */
  now?:          number;
  correlationId?: string;
  timestamp?:    string;
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

const INITIAL_STATE: InternalState = {
  agents: [], activeMissions: 0, pendingApprovals: 0,
  overallHealth: 'healthy', teamPairs: [], messagePairs: [], recentMessages: [],
};

function reducer(s: InternalState, action: Action): InternalState {
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
      return { ...s, teamPairs: [...s.teamPairs, pair].slice(-5) };
    }
    return s;
  }
  if (action.type === 'org.mission.completed' || action.type === 'org.mission.failed') {
    return { ...s, teamPairs: [] };
  }
  if (action.type === 'org.collaboration.message') {
    const from = String(action.payload.from_agent ?? '');
    if (!from) return s;
    const toRaw = String(action.payload.to ?? 'team');
    const kind = String(action.payload.kind ?? 'update');
    const target = toRaw === 'team' ? '__hub__' : toRaw;
    const now = action.now ?? Date.now();
    const id = action.correlationId || `${from}-${action.timestamp ?? now}`;
    const message: OrgRecentMessage = { id, from, to: target, kind, at: now };
    const messagePairs = [
      ...s.messagePairs,
      { pair: [from, target] as [string, string], expiresAt: now + MESSAGE_BEAM_DECAY_MS },
    ].slice(-MESSAGE_PAIRS_CAP);
    const recentMessages = [message, ...s.recentMessages].slice(0, RECENT_MESSAGES_CAP);
    return { ...s, messagePairs, recentMessages };
  }
  if (action.type === '__prune_expired__') {
    const now = action.now ?? Date.now();
    const messagePairs = s.messagePairs.filter(mp => mp.expiresAt > now);
    if (messagePairs.length === s.messagePairs.length) return s;
    return { ...s, messagePairs };
  }
  return s;
}

export function useOrgNeuralState(orgId: string | null): OrgNeuralState {
  const { data: health } = useOrgHealth(orgId);

  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  // Sync health data
  const healthRef = useRef(health);
  healthRef.current = health;

  const applyEvent = useCallback((event: OrgEvent) => {
    dispatch({
      type:          event.event_type,
      payload:       event.payload,
      now:           Date.now(),
      correlationId: event.correlation_id,
      timestamp:     event.timestamp,
    });
  }, []);

  // Message-driven comm pairs are transient — nothing else forces a re-render
  // once the last real event settles, so tick a lightweight prune while any
  // are pending. This never runs (and costs nothing) when there's nothing to
  // decay, and it only prunes/re-renders — it never drives the pairs' actual
  // expiry math (that's computed fresh below on every read).
  useEffect(() => {
    if (state.messagePairs.length === 0) return;
    const timer = setInterval(() => {
      dispatch({ type: '__prune_expired__', payload: {}, now: Date.now() });
    }, 1000);
    return () => clearInterval(timer);
  }, [state.messagePairs.length]);

  const now = Date.now();
  const communicatingPairs: [string, string][] = [
    ...state.teamPairs,
    ...state.messagePairs.filter(mp => mp.expiresAt > now).map(mp => mp.pair),
  ];

  const result: OrgNeuralState = {
    agents:             state.agents,
    communicatingPairs,
    recentMessages:     state.recentMessages,
    activeMissions:     (health as any)?.active_missions  ?? state.activeMissions,
    pendingApprovals:   (health as any)?.pending_approvals ?? state.pendingApprovals,
    overallHealth:      (health as any)?.overall_health    ?? state.overallHealth,
    applyEvent,
  };

  return result;
}
