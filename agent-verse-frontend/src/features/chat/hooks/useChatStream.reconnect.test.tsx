/** Phase 7 — SSE reconnect with exponential backoff before goal completion. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from './useChatStream';

const mocks = vi.hoisted(() => ({ streamToken: vi.fn<() => Promise<string>>() }));
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    streamToken: mocks.streamToken,
    streamUrl: (_s: string, _m: string, token: string) => `http://x/stream?token=${token}`,
  },
}));

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

// open() awaits the stream-token mint, so both the initial connect and every
// reconnect need pending microtasks flushed (advanceTimersByTimeAsync does so).
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const start = async (result: { current: ReturnType<typeof useChatStream> }, id = 'm1') => {
  act(() => result.current.startStream(id));
  await advance(0);
};

beforeEach(() => {
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
  vi.useFakeTimers();
  let n = 0;
  mocks.streamToken.mockReset();
  mocks.streamToken.mockImplementation(() => Promise.resolve(`tok-${++n}`));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useChatStream reconnect/backoff', () => {
  it('retries a dropped connection and recovers on the next message', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    await start(result);
    const first = latest();

    // Connection drops before completion.
    act(() => first.onerror?.());
    expect(result.current.reconnecting).toBe(true);
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.error).toBeNull();
    expect(first.closed).toBe(true);

    // After the first backoff (500ms) a fresh EventSource opens.
    await advance(500);
    const second = latest();
    expect(second).not.toBe(first);
    expect(MockES.instances).toHaveLength(2);

    // A successful message clears the reconnecting flag and accumulates tokens.
    act(() => second.emit({ type: 'token', token: 'hello' }));
    expect(result.current.reconnecting).toBe(false);
    expect(result.current.tokens).toBe('hello');
  });

  it('uses increasing backoff and resets after a successful message', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    await start(result);

    act(() => latest().onerror?.());
    // Not yet reconnected before 500ms elapses.
    await advance(499);
    expect(MockES.instances).toHaveLength(1);
    await advance(1);
    expect(MockES.instances).toHaveLength(2);

    // Second drop → backoff doubles to 1000ms.
    act(() => latest().onerror?.());
    await advance(999);
    expect(MockES.instances).toHaveLength(2);
    await advance(1);
    expect(MockES.instances).toHaveLength(3);

    // A message resets the backoff, so the next drop reconnects after 500ms again.
    act(() => latest().emit({ type: 'token', token: 'x' }));
    act(() => latest().onerror?.());
    await advance(500);
    expect(MockES.instances).toHaveLength(4);
    expect(result.current.error).toBeNull();
  });

  it('gives up after the retry cap and surfaces a hard error', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    await start(result);

    // 5 retries (each reconnects), then the 6th drop exceeds the cap.
    for (let i = 0; i < 5; i++) {
      act(() => latest().onerror?.());
      await advance(8000);
    }
    act(() => latest().onerror?.());

    expect(result.current.error).toBeTruthy();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.reconnecting).toBe(false);
  });

  it('does not reconnect after a terminal done event', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    await start(result);
    act(() => latest().emit({ type: 'done' }));
    const count = MockES.instances.length;

    act(() => latest().onerror?.());
    await advance(8000);

    expect(MockES.instances).toHaveLength(count); // no new connection opened
    expect(result.current.isStreaming).toBe(false);
  });

  it('mints a fresh stream token for every reconnect (token in the URL, never api_key)', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    await start(result);
    expect(latest().url).toBe('http://x/stream?token=tok-1');

    act(() => latest().onerror?.());
    await advance(500);
    expect(mocks.streamToken).toHaveBeenCalledTimes(2);
    expect(latest().url).toBe('http://x/stream?token=tok-2');
    for (const es of MockES.instances) expect(es.url).not.toContain('api_key=');
  });

  it('a failed token mint on reconnect opens no new EventSource and surfaces an error', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    await start(result);
    mocks.streamToken.mockRejectedValue(new Error('HTTP 401'));

    act(() => latest().onerror?.());
    await advance(500);

    expect(MockES.instances).toHaveLength(1);
    expect(result.current.error).toBe('Could not authorize the stream');
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.reconnecting).toBe(false);

    // Terminal: nothing further is retried.
    await advance(10_000);
    expect(MockES.instances).toHaveLength(1);
  });
});
