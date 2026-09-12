/**
 * useGoalExecutionGraph — consumes useGoalStream events and builds a live DAG.
 * Spec §4: Returns nodes + edges that GoalExecutionGraph renders as React Flow.
 */
import { useReducer, useEffect, useRef } from 'react';
import type { GoalEvent } from '@/lib/sse/useGoalStream';

export type GraphNodeType =
  | 'start' | 'plan' | 'step' | 'tool' | 'verify'
  | 'hitl' | 'guardrail' | 'knowledge' | 'replan' | 'complete' | 'failed';

export type GraphNodeStatus =
  | 'pending' | 'active' | 'done' | 'failed' | 'blocked' | 'waiting';

export interface GraphNode {
  id:       string;
  type:     GraphNodeType;
  label:    string;
  status:   GraphNodeStatus;
  position: { x: number; y: number };
  data:     Record<string, unknown>;
}

export interface GraphEdge {
  id:     string;
  source: string;
  target: string;
  animated: boolean;
  color:    string;
}

export interface GoalExecutionGraph {
  nodes:       GraphNode[];
  edges:       GraphEdge[];
  activeNodeId: string | null;
  tokenStats:  { input: number; output: number; costUsd: number; tps: number };
  guardrailStats: { fired: number; blocked: number; lastRule?: string };
  hitlStats:   { pending: number; approved: number; rejected: number };
}

type Action =
  | { type: 'RESET' }
  | { type: 'EVT'; evt: GoalEvent };

function buildInitialState(): GoalExecutionGraph {
  return {
    nodes: [{ id: 'start', type: 'start', label: 'Goal', status: 'active', position: { x: 200, y: 20 }, data: {} }],
    edges: [],
    activeNodeId: 'start',
    tokenStats:   { input: 0, output: 0, costUsd: 0, tps: 0 },
    guardrailStats: { fired: 0, blocked: 0 },
    hitlStats:   { pending: 0, approved: 0, rejected: 0 },
  };
}

let _stepCounter    = 0;
let _toolCounter    = 0;
let _knowledgeCounter = 0;

function addNode(state: GoalExecutionGraph, node: GraphNode, fromId: string, color = '#1E2C4A'): GoalExecutionGraph {
  const exists = state.nodes.find(n => n.id === node.id);
  const nodes  = exists
    ? state.nodes.map(n => n.id === node.id ? { ...n, ...node } : n)
    : [...state.nodes, node];
  const edgeId = `e-${fromId}-${node.id}`;
  const edges  = state.edges.find(e => e.id === edgeId)
    ? state.edges
    : [...state.edges, { id: edgeId, source: fromId, target: node.id, animated: true, color }];
  return { ...state, nodes, edges, activeNodeId: node.id };
}

function updateNode(state: GoalExecutionGraph, id: string, patch: Partial<GraphNode>): GoalExecutionGraph {
  return { ...state, nodes: state.nodes.map(n => n.id === id ? { ...n, ...patch } : n) };
}

function reducer(state: GoalExecutionGraph, action: Action): GoalExecutionGraph {
  if (action.type === 'RESET') { _stepCounter = 0; _toolCounter = 0; _knowledgeCounter = 0; return buildInitialState(); }

  const { evt } = action;
  const lastStepId = () => `step-${_stepCounter}`;

  switch (evt.type) {

    case 'plan_ready': {
      const steps = Array.isArray(evt.steps) ? (evt.steps as string[]) : [];
      const planNode: GraphNode = {
        id: 'plan', type: 'plan', label: `Plan (${steps.length} steps)`,
        status: 'done', position: { x: 200, y: 100 },
        data: { steps },
      };
      let s = addNode(state, planNode, 'start', '#6366F1');
      steps.forEach((step, i) => {
        const sid = `step-${i + 1}`;
        const from = i === 0 ? 'plan' : `step-${i}`;
        s = addNode(s, {
          id: sid, type: 'step', label: String(step).slice(0, 60),
          status: 'pending', position: { x: 200, y: 200 + i * 120 }, data: { step },
        }, from, '#1E2C4A');
      });
      return s;
    }

    case 'step_started': {
      _stepCounter++;
      const sid = lastStepId();
      const label = typeof evt.step === 'string' ? evt.step.slice(0, 60) : `Step ${_stepCounter}`;
      const existingNode = state.nodes.find(n => n.type === 'step' && n.status === 'pending');
      if (existingNode) {
        return updateNode(state, existingNode.id, { status: 'active', label, data: { step: evt.step } });
      }
      const from = [...state.nodes].reverse().find((n: GraphNode) => n.type === 'step' && n.status === 'done')?.id ?? 'plan';
      return { ...addNode(state, {
        id: sid, type: 'step', label, status: 'active',
        position: { x: 200, y: 200 + _stepCounter * 120 }, data: { step: evt.step },
      }, from, '#6366F1'), activeNodeId: sid };
    }

    case 'step_complete': {
      const active = state.nodes.find(n => n.type === 'step' && n.status === 'active');
      if (!active) return state;
      return updateNode(state, active.id, { status: 'done', data: { ...active.data, output: evt.output } });
    }

    case 'tool_call_complete':
    case 'tool_call_failed': {
      _toolCounter++;
      const toolName = String(evt.tool_name ?? evt.tool ?? `Tool ${_toolCounter}`).slice(0, 40);
      const tid      = `tool-${_toolCounter}`;
      const fromStep = [...state.nodes].reverse().find((n: GraphNode) => n.type === 'step' && n.status === 'active')?.id ?? 'plan';
      const success  = evt.type === 'tool_call_complete';
      return addNode(state, {
        id: tid, type: 'tool', label: toolName,
        status: success ? 'done' : 'failed', position: { x: 400, y: 200 + _toolCounter * 80 },
        data: { tool: toolName, success },
      }, fromStep, success ? '#00E676' : '#FF3366');
    }

    case 'tool_call_pending_approval':
    case 'waiting_approval': {
      const rid = String(evt.request_id ?? 'hitl');
      return { ...addNode(state, {
        id: `hitl-${rid}`, type: 'hitl', label: 'Awaiting Approval',
        status: 'waiting', position: { x: 200, y: 200 + (_stepCounter + 1) * 120 },
        data: { request_id: rid, action: evt.action },
      }, lastStepId(), '#FFB300'), hitlStats: { ...state.hitlStats, pending: state.hitlStats.pending + 1 } };
    }

    case 'approval_granted':
    case 'hitl_approved': {
      const rid2 = String(evt.request_id ?? '');
      const node = state.nodes.find(n => n.id === `hitl-${rid2}`);
      if (!node) return { ...state, hitlStats: { ...state.hitlStats, approved: state.hitlStats.approved + 1, pending: Math.max(0, state.hitlStats.pending - 1) } };
      return { ...updateNode(state, node.id, { status: 'done' }), hitlStats: { ...state.hitlStats, approved: state.hitlStats.approved + 1, pending: Math.max(0, state.hitlStats.pending - 1) } };
    }

    case 'hitl_rejected': {
      return { ...state, hitlStats: { ...state.hitlStats, rejected: state.hitlStats.rejected + 1, pending: Math.max(0, state.hitlStats.pending - 1) } };
    }

    case 'guardrail_rejected':
    case 'tool_call_denied': {
      const rule = String(evt.rule ?? evt.reason ?? 'policy');
      const gid  = `guardrail-${state.guardrailStats.fired + 1}`;
      return { ...addNode(state, {
        id: gid, type: 'guardrail', label: `Blocked: ${rule.slice(0, 30)}`,
        status: 'blocked', position: { x: 400, y: 200 + _toolCounter * 80 + 40 },
        data: { rule },
      }, lastStepId(), '#FF3366'),
        guardrailStats: { fired: state.guardrailStats.fired + 1, blocked: state.guardrailStats.blocked + 1, lastRule: rule },
      };
    }

    case 'knowledge_retrieved': {
      _knowledgeCounter++;
      const kid   = `knowledge-${_knowledgeCounter}`;
      const from  = [...state.nodes].reverse().find((n: GraphNode) => n.type === 'step' && n.status === 'active')?.id ?? 'plan';
      return addNode(state, {
        id: kid, type: 'knowledge', label: 'Knowledge hit',
        status: 'done', position: { x: 0, y: 200 + _knowledgeCounter * 60 },
        data: {},
      }, from, '#34D399');
    }

    case 'verification_done': {
      const success2 = evt.success !== false;
      return addNode(state, {
        id: 'verify', type: 'verify', label: success2 ? 'Verified ✓' : 'Verify failed',
        status: success2 ? 'done' : 'failed', position: { x: 200, y: 200 + (_stepCounter + 2) * 120 },
        data: {},
      }, lastStepId(), success2 ? '#00E676' : '#FF3366');
    }

    case 'replan': {
      return addNode(state, {
        id: `replan-${Date.now()}`, type: 'replan', label: 'Replanning…',
        status: 'active', position: { x: 350, y: 200 + (_stepCounter + 1) * 120 },
        data: { reason: evt.reason },
      }, 'verify', '#FFB300');
    }

    case 'goal_complete': {
      return addNode(state, {
        id: 'complete', type: 'complete', label: 'Complete ✓',
        status: 'done', position: { x: 200, y: 200 + (_stepCounter + 3) * 120 },
        data: {},
      }, 'verify', '#00E676');
    }

    case 'goal_failed': {
      return addNode(state, {
        id: 'failed', type: 'failed', label: 'Failed ✗',
        status: 'failed', position: { x: 200, y: 200 + (_stepCounter + 3) * 120 },
        data: { reason: evt.reason },
      }, lastStepId(), '#FF3366');
    }

    case 'token_chunk': {
      const c = state.tokenStats;
      const text = String(evt.cumulative ?? '');
      const newOut = Math.max(c.output, text.length);
      return { ...state, tokenStats: { ...c, output: newOut } };
    }

    default: return state;
  }
}

export function useGoalExecutionGraph(events: GoalEvent[]): GoalExecutionGraph {
  const [state, dispatch] = useReducer(reducer, undefined, buildInitialState);
  const lastLen = useRef(0);

  useEffect(() => {
    if (events.length === 0 && lastLen.current > 0) {
      dispatch({ type: 'RESET' });
      lastLen.current = 0;
      return;
    }
    const newEvts = events.slice(lastLen.current);
    lastLen.current = events.length;
    for (const evt of newEvts) {
      // Persisted replay events nest their payload under `data` ({type, ts, data}),
      // while live SSE events are flat. Flatten so the reducer's field reads
      // (evt.steps, evt.step, evt.output, …) work for both shapes — otherwise a
      // completed goal's graph renders empty (plan_ready with 0 steps).
      const data = (evt as { data?: Record<string, unknown> }).data;
      const flat = data && typeof data === 'object' ? { ...data, ...evt } : evt;
      dispatch({ type: 'EVT', evt: flat as GoalEvent });
    }
  }, [events]);

  return state;
}
