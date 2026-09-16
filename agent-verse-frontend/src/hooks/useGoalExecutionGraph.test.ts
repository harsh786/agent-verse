/**
 * Tests for useGoalExecutionGraph — the reducer that turns a stream of
 * GoalEvents into a live DAG (nodes/edges) plus token/guardrail/HITL stats.
 *
 * The reducer keeps a few module-scoped counters (_stepCounter, _toolCounter,
 * _knowledgeCounter) that only reset on the 'RESET' action, so each test
 * re-imports the module fresh (vi.resetModules) to avoid cross-test bleed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { GoalEvent } from '@/lib/sse/useGoalStream';

let useGoalExecutionGraph: typeof import('./useGoalExecutionGraph').useGoalExecutionGraph;

beforeEach(async () => {
  vi.resetModules();
  ({ useGoalExecutionGraph } = await import('./useGoalExecutionGraph'));
});

function ev(partial: { type: string } & Record<string, unknown>): GoalEvent {
  return partial as GoalEvent;
}

describe('useGoalExecutionGraph — initial state', () => {
  it('starts with a single active "start" node and zeroed stats', () => {
    const { result } = renderHook(() => useGoalExecutionGraph([]));
    expect(result.current.nodes).toHaveLength(1);
    expect(result.current.nodes[0]).toMatchObject({ id: 'start', type: 'start', status: 'active' });
    expect(result.current.edges).toEqual([]);
    expect(result.current.activeNodeId).toBe('start');
    expect(result.current.tokenStats).toEqual({ input: 0, output: 0, costUsd: 0, tps: 0 });
    expect(result.current.guardrailStats).toEqual({ fired: 0, blocked: 0 });
    expect(result.current.hitlStats).toEqual({ pending: 0, approved: 0, rejected: 0 });
  });
});

describe('useGoalExecutionGraph — plan_ready', () => {
  it('adds a plan node and one step node per step', () => {
    const events = [ev({ type: 'plan_ready', steps: ['Do A', 'Do B'] })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.map((n) => n.id)).toEqual(['start', 'plan', 'step-1', 'step-2']);
    expect(result.current.nodes.find((n) => n.id === 'plan')?.label).toBe('Plan (2 steps)');
    expect(result.current.edges).toHaveLength(3);
  });

  it('tolerates a non-array steps field, treating it as zero steps', () => {
    const events = [ev({ type: 'plan_ready', steps: 'not-an-array' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'plan')?.label).toBe('Plan (0 steps)');
    expect(result.current.nodes.map((n) => n.id)).toEqual(['start', 'plan']);
  });
});

describe('useGoalExecutionGraph — step_started', () => {
  it('activates an existing pending step created by plan_ready', () => {
    const events = [
      ev({ type: 'plan_ready', steps: ['Step one'] }),
      ev({ type: 'step_started', step: 'Step one' }),
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const step = result.current.nodes.find((n) => n.id === 'step-1');
    expect(step?.status).toBe('active');
    expect(step?.data).toEqual({ step: 'Step one' });
  });

  it('creates a fresh step node when there is no pending step (no prior plan)', () => {
    const events = [ev({ type: 'step_started', step: 'Solo step' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const step = result.current.nodes.find((n) => n.id === 'step-1');
    expect(step).toBeDefined();
    expect(step?.status).toBe('active');
    expect(step?.label).toBe('Solo step');
    expect(result.current.activeNodeId).toBe('step-1');
  });

  it('falls back to a generic "Step N" label when evt.step is not a string', () => {
    const events = [ev({ type: 'step_started', step: 42 })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const step = result.current.nodes.find((n) => n.id === 'step-1');
    expect(step?.label).toBe('Step 1');
  });

  it('chains from the last done step when creating a new step node', () => {
    const events = [
      ev({ type: 'step_started', step: 'first' }),
      ev({ type: 'step_complete', output: 'ok' }),
      ev({ type: 'step_started', step: 'second' }),
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const edgeToSecond = result.current.edges.find((e) => e.target === 'step-2');
    expect(edgeToSecond?.source).toBe('step-1');
  });
});

describe('useGoalExecutionGraph — step_complete', () => {
  it('marks the active step done and records its output', () => {
    const events = [
      ev({ type: 'step_started', step: 'first' }),
      ev({ type: 'step_complete', output: 'result-data' }),
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const step = result.current.nodes.find((n) => n.id === 'step-1');
    expect(step?.status).toBe('done');
    expect(step?.data).toMatchObject({ output: 'result-data' });
  });

  it('is a no-op when there is no active step', () => {
    const events = [ev({ type: 'step_complete', output: 'x' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes).toHaveLength(1); // just "start"
  });
});

describe('useGoalExecutionGraph — tool calls', () => {
  it('tool_call_complete adds a done tool node named from tool_name', () => {
    const events = [
      ev({ type: 'step_started', step: 'first' }),
      ev({ type: 'tool_call_complete', tool_name: 'search_web' }),
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const tool = result.current.nodes.find((n) => n.id === 'tool-1');
    expect(tool).toMatchObject({ type: 'tool', label: 'search_web', status: 'done' });
  });

  it('tool_call_failed adds a failed tool node, falling back to `tool` then a counter label', () => {
    const events = [
      ev({ type: 'step_started', step: 'first' }),
      ev({ type: 'tool_call_failed', tool: 'legacy_tool_field' }),
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const tool = result.current.nodes.find((n) => n.id === 'tool-1');
    expect(tool).toMatchObject({ label: 'legacy_tool_field', status: 'failed' });
  });

  it('falls back to a generic "Tool N" label when neither tool_name nor tool is present', () => {
    const events = [ev({ type: 'tool_call_complete' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const tool = result.current.nodes.find((n) => n.id === 'tool-1');
    expect(tool?.label).toBe('Tool 1');
  });
});

describe('useGoalExecutionGraph — HITL flow', () => {
  it('waiting_approval / tool_call_pending_approval add a waiting node and increment pending', () => {
    const events = [ev({ type: 'waiting_approval', request_id: 'req-1', action: 'delete_prod' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'hitl-req-1')?.status).toBe('waiting');
    expect(result.current.hitlStats.pending).toBe(1);
  });

  it('falls back to a generic "hitl" request id when none is provided', () => {
    const events = [ev({ type: 'tool_call_pending_approval' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'hitl-hitl')).toBeDefined();
  });

  it('approval_granted / hitl_approved marks the matching node done and updates stats', () => {
    const events = [
      ev({ type: 'waiting_approval', request_id: 'req-1' }),
      ev({ type: 'hitl_approved', request_id: 'req-1' }),
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const node = result.current.nodes.find((n) => n.id === 'hitl-req-1');
    expect(node?.status).toBe('done');
    expect(result.current.hitlStats).toEqual({ pending: 0, approved: 1, rejected: 0 });
  });

  it('approval_granted with no matching node still updates stats (node not found branch)', () => {
    const events = [ev({ type: 'approval_granted', request_id: 'unknown' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.hitlStats).toEqual({ pending: 0, approved: 1, rejected: 0 });
  });

  it('hitl_rejected increments rejected and decrements pending (floored at 0)', () => {
    const events = [ev({ type: 'hitl_rejected' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.hitlStats).toEqual({ pending: 0, approved: 0, rejected: 1 });
  });
});

describe('useGoalExecutionGraph — guardrails', () => {
  it('guardrail_rejected adds a blocked node and increments fired+blocked', () => {
    const events = [ev({ type: 'guardrail_rejected', rule: 'no_prod_deletes' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const node = result.current.nodes.find((n) => n.id === 'guardrail-1');
    expect(node?.status).toBe('blocked');
    expect(node?.label).toContain('no_prod_deletes');
    expect(result.current.guardrailStats).toEqual({ fired: 1, blocked: 1, lastRule: 'no_prod_deletes' });
  });

  it('tool_call_denied falls back to evt.reason, then to "policy"', () => {
    const events1 = [ev({ type: 'tool_call_denied', reason: 'budget exceeded' })];
    const { result: r1 } = renderHook(() => useGoalExecutionGraph(events1));
    expect(r1.current.guardrailStats.lastRule).toBe('budget exceeded');

    const events2 = [ev({ type: 'tool_call_denied' })];
    const { result: r2 } = renderHook(() => useGoalExecutionGraph(events2));
    expect(r2.current.guardrailStats.lastRule).toBe('policy');
  });
});

describe('useGoalExecutionGraph — knowledge, verification, replan, completion', () => {
  it('knowledge_retrieved adds a knowledge node', () => {
    const events = [ev({ type: 'knowledge_retrieved' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'knowledge-1')).toMatchObject({ type: 'knowledge', status: 'done' });
  });

  it('verification_done(success) adds a done "Verified" node', () => {
    const events = [ev({ type: 'verification_done', success: true })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'verify')).toMatchObject({ status: 'done', label: 'Verified ✓' });
  });

  it('verification_done(success=false) adds a failed node', () => {
    const events = [ev({ type: 'verification_done', success: false })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'verify')).toMatchObject({ status: 'failed', label: 'Verify failed' });
  });

  it('verification_done treats a missing success field as success (success !== false)', () => {
    const events = [ev({ type: 'verification_done' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'verify')?.status).toBe('done');
  });

  it('replan adds an active replanning node', () => {
    const events = [ev({ type: 'replan', reason: 'tool failed' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    const node = result.current.nodes.find((n) => n.type === 'replan');
    expect(node).toMatchObject({ status: 'active', label: 'Replanning…', data: { reason: 'tool failed' } });
  });

  it('goal_complete adds a done "complete" node', () => {
    const events = [ev({ type: 'goal_complete' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'complete')).toMatchObject({ status: 'done' });
  });

  it('goal_failed adds a failed node with the failure reason', () => {
    const events = [ev({ type: 'goal_failed', reason: 'budget exhausted' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes.find((n) => n.id === 'failed')).toMatchObject({
      status: 'failed',
      data: { reason: 'budget exhausted' },
    });
  });
});

describe('useGoalExecutionGraph — token_chunk and unknown events', () => {
  it('token_chunk grows output to the max cumulative text length seen', () => {
    const events = [
      ev({ type: 'token_chunk', cumulative: 'ab' }),
      ev({ type: 'token_chunk', cumulative: 'abcdef' }),
      ev({ type: 'token_chunk', cumulative: 'a' }), // shorter — should not shrink
    ];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.tokenStats.output).toBe(6);
  });

  it('ignores unknown event types (default branch returns state unchanged)', () => {
    const events = [ev({ type: 'some_unhandled_event' })];
    const { result } = renderHook(() => useGoalExecutionGraph(events));
    expect(result.current.nodes).toHaveLength(1);
  });
});

describe('useGoalExecutionGraph — event stream mechanics', () => {
  it('processes only newly appended events across re-renders', () => {
    const { result, rerender } = renderHook(({ events }) => useGoalExecutionGraph(events), {
      initialProps: { events: [ev({ type: 'plan_ready', steps: ['A'] })] as GoalEvent[] },
    });
    expect(result.current.nodes.map((n) => n.id)).toEqual(['start', 'plan', 'step-1']);

    rerender({
      events: [
        ev({ type: 'plan_ready', steps: ['A'] }),
        ev({ type: 'step_started', step: 'A' }),
      ],
    });
    expect(result.current.nodes.find((n) => n.id === 'step-1')?.status).toBe('active');
  });

  it('resets the graph when the events array goes from non-empty back to empty', () => {
    const { result, rerender } = renderHook(({ events }) => useGoalExecutionGraph(events), {
      initialProps: { events: [ev({ type: 'plan_ready', steps: ['A', 'B'] })] as GoalEvent[] },
    });
    expect(result.current.nodes.length).toBeGreaterThan(1);

    rerender({ events: [] });
    expect(result.current.nodes).toHaveLength(1);
    expect(result.current.nodes[0].id).toBe('start');
  });

  it('flattens persisted replay events (payload nested under `data`) before dispatching', () => {
    const replayEvent = { type: 'plan_ready', ts: 123, data: { steps: ['Replayed step'] } } as unknown as GoalEvent;
    const { result } = renderHook(() => useGoalExecutionGraph([replayEvent]));
    expect(result.current.nodes.find((n) => n.id === 'plan')?.label).toBe('Plan (1 steps)');
    expect(result.current.nodes.find((n) => n.id === 'step-1')).toBeDefined();
  });

  it('handles a live (flat, non-nested) event unchanged', () => {
    const flatEvent = ev({ type: 'plan_ready', steps: ['Flat step'] });
    const { result } = renderHook(() => useGoalExecutionGraph([flatEvent]));
    expect(result.current.nodes.find((n) => n.id === 'plan')?.label).toBe('Plan (1 steps)');
  });
});
