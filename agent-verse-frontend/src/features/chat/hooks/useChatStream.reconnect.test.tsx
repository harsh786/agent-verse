/** Phase 7 — SSE reconnect with exponential backoff before goal completion. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from './useChatStream';

vi.mock('@/lib/api/chat', () => ({ chatApi: { streamUrl: () => 'http://x/stream' } }));

class MockES {
  static instances: MockES[] = [];
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    MockES.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

const latest = () => MockES.instances[MockES.instances.length - 1];

beforeEach(() => {
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useChatStream reconnect/backoff', () => {
  it('retries a dropped connection and recovers on the next message', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
    const first = latest();

    // Connection drops before completion.
    act(() => first.onerror?.());
    expect(result.current.reconnecting).toBe(true);
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.error).toBeNull();
    expect(first.closed).toBe(true);

    // After the first backoff (500ms) a fresh EventSource opens.
    act(() => vi.advanceTimersByTime(500));
    const second = latest();
    expect(second).not.toBe(first);
    expect(MockES.instances).toHaveLength(2);

    // A successful message clears the reconnecting flag and accumulates tokens.
    act(() => second.emit({ type: 'token', token: 'hello' }));
    expect(result.current.reconnecting).toBe(false);
    expect(result.current.tokens).toBe('hello');
  });

  it('uses increasing backoff and resets after a successful message', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));

    act(() => latest().onerror?.());
    // Not yet reconnected before 500ms elapses.
    act(() => vi.advanceTimersByTime(499));
    expect(MockES.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1));
    expect(MockES.instances).toHaveLength(2);

    // Second drop → backoff doubles to 1000ms.
    act(() => latest().onerror?.());
    act(() => vi.advanceTimersByTime(999));
    expect(MockES.instances).toHaveLength(2);
    act(() => vi.advanceTimersByTime(1));
    expect(MockES.instances).toHaveLength(3);

    // A message resets the backoff, so the next drop reconnects after 500ms again.
    act(() => latest().emit({ type: 'token', token: 'x' }));
    act(() => latest().onerror?.());
    act(() => vi.advanceTimersByTime(500));
    expect(MockES.instances).toHaveLength(4);
    expect(result.current.error).toBeNull();
  });

  it('gives up after the retry cap and surfaces a hard error', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));

    // 5 retries (each reconnects), then the 6th drop exceeds the cap.
    for (let i = 0; i < 5; i++) {
      act(() => latest().onerror?.());
      act(() => vi.advanceTimersByTime(8000));
    }
    act(() => latest().onerror?.());

    expect(result.current.error).toBeTruthy();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.reconnecting).toBe(false);
  });

  it('does not reconnect after a terminal done event', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => result.current.startStream('m1'));
    act(() => latest().emit({ type: 'done' }));
    const count = MockES.instances.length;

    act(() => latest().onerror?.());
    act(() => vi.advanceTimersByTime(8000));

    expect(MockES.instances).toHaveLength(count); // no new connection opened
    expect(result.current.isStreaming).toBe(false);
  });
});
