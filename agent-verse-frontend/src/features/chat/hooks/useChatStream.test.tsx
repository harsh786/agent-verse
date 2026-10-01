/** Phase 7 — the stream hook accumulates the structural event sequence. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChatStream } from './useChatStream';

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    streamToken: () => Promise.resolve('tok'),
    streamUrl: (_s: string, _m: string, token: string) => `http://x/stream?token=${token}`,
  },
}));

class MockES {
  static instances: MockES[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { MockES.instances.push(this); }
  close() { this.closed = true; }
  emit(obj: unknown) { this.onmessage?.({ data: JSON.stringify(obj) }); }
}

beforeEach(() => {
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
});

describe('useChatStream accumulation', () => {
  it('accumulates structural events in order and skips token/reasoning', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
    await waitFor(() => expect(MockES.instances).toHaveLength(1));
    const es = MockES.instances[0];
    act(() => {
      es.emit({ type: 'plan_ready', steps: ['a'] });
      es.emit({ type: 'token', token: 'hi ' });
      es.emit({ type: 'tool_call', tool: 'jira.search' });
      es.emit({ type: 'reasoning', token: 'thinking' });
      es.emit({ type: 'token', token: 'there' });
      es.emit({ type: 'goal_complete' });
      es.emit({ type: 'done' });
    });
    expect(result.current.events.map((e) => e.type)).toEqual([
      'plan_ready', 'tool_call', 'goal_complete', 'done',
    ]);
    expect(result.current.tokens).toBe('hi there');
    expect(result.current.isStreaming).toBe(false);
  });

  it('resets the accumulator on a new stream', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
    await waitFor(() => expect(MockES.instances).toHaveLength(1));
    act(() => MockES.instances[0].emit({ type: 'plan_ready' }));
    expect(result.current.events).toHaveLength(1);
    act(() => result.current.startStream('m2'));
    expect(result.current.events).toHaveLength(0);
  });
});

describe('useChatStream budget refusal', () => {
  it('names an exhausted LLM budget instead of a generic stream error', async () => {
    const { LLM_BUDGET_EXHAUSTED_MESSAGE } = await import('@/lib/api/client');
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
    await waitFor(() => expect(MockES.instances).toHaveLength(1));
    act(() => MockES.instances[0].emit({
      type: 'error', code: 'llm_budget_exhausted', message: 'LLM budget exhausted (x)',
    }));
    expect(result.current.error).toBe(LLM_BUDGET_EXHAUSTED_MESSAGE);
    expect(result.current.isStreaming).toBe(false);
  });
});
