/** Phase 7 — the stream hook accumulates the structural event sequence. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from './useChatStream';

vi.mock('@/lib/api/chat', () => ({ chatApi: { streamUrl: () => 'http://x/stream' } }));

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
  it('accumulates structural events in order and skips token/reasoning', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
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

  it('resets the accumulator on a new stream', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
    act(() => MockES.instances[0].emit({ type: 'plan_ready' }));
    expect(result.current.events).toHaveLength(1);
    act(() => result.current.startStream('m2'));
    expect(result.current.events).toHaveLength(0);
  });
});
