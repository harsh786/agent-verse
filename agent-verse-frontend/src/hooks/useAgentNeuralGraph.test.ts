import { renderHook } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { useAgentNeuralGraph } from './useAgentNeuralGraph';

type Evt = { event_type?: string; payload?: Record<string, unknown> };

describe('useAgentNeuralGraph', () => {
  test('starts from an empty graph', () => {
    const { result } = renderHook(() => useAgentNeuralGraph([]));
    expect(result.current.agents.size).toBe(0);
    expect(result.current.edges).toEqual([]);
    expect(result.current.communicatingPairs).toEqual([]);
  });

  test('adds an agent and maps its event type to a neural state', () => {
    const { result, rerender } = renderHook(({ e }: { e: Evt[] }) => useAgentNeuralGraph(e), {
      initialProps: { e: [] as Evt[] },
    });
    rerender({
      e: [{ event_type: 'org.agent.activated', payload: { agent_id: 'a1', agent_name: 'Coordinator' } }],
    });
    expect(result.current.agents.size).toBe(1);
    const node = result.current.agents.get('a1');
    expect(node?.state).toBe('executing');
    expect(node?.label).toBe('Coordinator');
  });

  test('applies subsequent state transitions to the same agent', () => {
    const events: Evt[] = [
      { event_type: 'org.agent.activated', payload: { agent_id: 'a1' } },
    ];
    const { result, rerender } = renderHook(({ e }: { e: Evt[] }) => useAgentNeuralGraph(e), {
      initialProps: { e: events },
    });
    expect(result.current.agents.get('a1')?.state).toBe('executing');

    rerender({ e: [...events, { event_type: 'org.agent.failed', payload: { agent_id: 'a1' } }] });
    expect(result.current.agents.get('a1')?.state).toBe('error');
  });

  test('a started mission records a communicating pair, and completion clears it', () => {
    const start: Evt[] = [
      { event_type: 'org.mission.started', payload: { agent_ids: ['a1', 'a2'] } },
    ];
    const { result, rerender } = renderHook(({ e }: { e: Evt[] }) => useAgentNeuralGraph(e), {
      initialProps: { e: start },
    });
    expect(result.current.communicatingPairs).toEqual([['a1', 'a2']]);

    rerender({ e: [...start, { event_type: 'org.mission.completed', payload: {} }] });
    expect(result.current.communicatingPairs).toEqual([]);
  });

  test('ignores agent events without an agent id and falls back to a truncated label', () => {
    const { result, rerender } = renderHook(({ e }: { e: Evt[] }) => useAgentNeuralGraph(e), {
      initialProps: { e: [] as Evt[] },
    });
    rerender({ e: [{ event_type: 'org.agent.activated', payload: {} }] });
    expect(result.current.agents.size).toBe(0);

    rerender({
      e: [
        { event_type: 'org.agent.activated', payload: {} },
        { event_type: 'org.agent.idle', payload: { agent_id: 'agent-12345678' } },
      ],
    });
    const node = result.current.agents.get('agent-12345678');
    expect(node?.state).toBe('idle');
    // label falls back to agent_id.slice(-8)
    expect(node?.label).toBe('12345678');
  });
});
