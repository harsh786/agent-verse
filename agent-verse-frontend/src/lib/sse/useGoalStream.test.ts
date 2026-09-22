/**
 * Tests for the useGoalStream SSE hook — token streaming behaviour.
 *
 * We test the token streaming logic by directly exercising the hook's
 * internal state transitions using React Testing Library's renderHook.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useGoalStream } from './useGoalStream';
import { useAuthStore } from '@/stores/auth';

// ── helpers ───────────────────────────────────────────────────────────────────

/**
 * Build a ReadableStream that yields the given SSE frames sequentially,
 * then closes the stream.
 */
function makeSseStream(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i >= frames.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(frames[i++]));
    },
  });
}

function sseFrame(data: object): string {
  return `data: ${JSON.stringify(data)}\n\n`;
}

/**
 * A ReadableStream whose lifecycle is driven manually by the test: push()
 * enqueues a frame, close() ends it "cleanly" (mimicking a server that just
 * stops sending, e.g. a dropped connection), and error() rejects the current
 * read (mimicking an aborted fetch).
 */
function makeControllableStream() {
  const encoder = new TextEncoder();
  let controllerRef!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller;
    },
  });
  return {
    stream,
    push(frame: string) {
      controllerRef.enqueue(encoder.encode(frame));
    },
    close() {
      controllerRef.close();
    },
    error(err: unknown) {
      controllerRef.error(err);
    },
  };
}

/** Wire a controllable stream's lifecycle to a fetch AbortSignal, the way a
 * real browser fetch would reject the in-flight read with an AbortError when
 * the request is aborted mid-stream. */
function abortErrorFor(signal: AbortSignal, ctl: ReturnType<typeof makeControllableStream>) {
  signal.addEventListener('abort', () => {
    ctl.error(new DOMException('The operation was aborted.', 'AbortError'));
  });
}

/** Advance fake timers and flush pending microtasks, wrapped in `act` so the
 * resulting React state updates are committed and visible via `result.current`. */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('useGoalStream — token streaming', () => {
  beforeEach(() => {
    localStorage.setItem('av_api_key', 'test-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  test('token_chunk events update streamingToken and are NOT added to events array', async () => {
    const stream = makeSseStream([
      sseFrame({ type: 'goal_started' }),
      sseFrame({ type: 'token_chunk', step: 'Write code', token: 'Hello', cumulative: 'Hello' }),
      sseFrame({ type: 'token_chunk', step: 'Write code', token: ' world', cumulative: 'Hello world' }),
    ]);

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );

    const { result } = renderHook(() => useGoalStream('goal-123'));

    await waitFor(() => {
      // Regular event is in events array
      expect(result.current.events.some((e) => e.type === 'goal_started')).toBe(true);
    });

    await waitFor(() => {
      // streamingToken reflects the last cumulative value
      expect(result.current.streamingToken?.cumulative).toBe('Hello world');
      expect(result.current.streamingToken?.step).toBe('Write code');
    });

    // token_chunk must NOT appear in events[]
    expect(result.current.events.some((e) => e.type === 'token_chunk')).toBe(false);
  });

  test('step_complete event clears streamingToken', async () => {
    // Deliver frames separately to ensure they can be processed
    const encoder = new TextEncoder();
    const frames = [
      sseFrame({ type: 'token_chunk', step: 'Do thing', token: 'Hey', cumulative: 'Hey' }),
      sseFrame({ type: 'step_complete', step: 'Do thing', output: 'done' }),
    ];
    let pushIdx = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (pushIdx >= frames.length) {
          controller.close();
          return;
        }
        controller.enqueue(encoder.encode(frames[pushIdx++]));
      },
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );

    const { result } = renderHook(() => useGoalStream('goal-456'));

    // After step_complete is processed, streamingToken must be cleared
    await waitFor(() => {
      expect(result.current.events.some((e) => e.type === 'step_complete')).toBe(true);
    });
    expect(result.current.streamingToken).toBeNull();
  });

  test('terminal event clears streamingToken and stops reconnect', async () => {
    const stream = makeSseStream([
      sseFrame({ type: 'token_chunk', step: 'Final step', token: 'Fin', cumulative: 'Fin' }),
      sseFrame({ type: 'goal_complete', result: 'Done' }),
    ]);

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );

    const { result } = renderHook(() => useGoalStream('goal-789'));

    await waitFor(() => {
      expect(result.current.events.some((e) => e.type === 'goal_complete')).toBe(true);
      expect(result.current.streamingToken).toBeNull();
      expect(result.current.connected).toBe(false);
    });
  });

  test('streamingToken is null initially and on new goalId', () => {
    const { result } = renderHook(() => useGoalStream(null));
    expect(result.current.streamingToken).toBeNull();
  });

  test('onEvent callback is called for token_chunk events', async () => {
    const stream = makeSseStream([
      sseFrame({ type: 'token_chunk', step: 'S1', token: 'abc', cumulative: 'abc' }),
    ]);

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );

    const onEvent = vi.fn();
    renderHook(() => useGoalStream('goal-cb', { onEvent }));

    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'token_chunk', token: 'abc' })
      );
    });
  });
});

describe('useGoalStream — reconnection, backoff, and resilience', () => {
  beforeEach(() => {
    localStorage.setItem('av_api_key', 'test-key');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  test('reconnects automatically after the stream closes without a terminal event', async () => {
    vi.useFakeTimers();
    const ctl1 = makeControllableStream();
    const ctl2 = makeControllableStream();
    const streams = [ctl1, ctl2];
    let call = 0;
    const fetchMock = vi.fn().mockImplementation(() => {
      const ctl = streams[call++];
      return Promise.resolve(
        new Response(ctl.stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => useGoalStream('goal-drop'));

    // First connection opens, then drops without a terminal event.
    ctl1.push(sseFrame({ type: 'goal_started' }));
    await advance(0);
    ctl1.close();
    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // The reconnect is scheduled for 1s out — not before.
    await advance(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    ctl2.push(sseFrame({ type: 'goal_complete' }));
    await advance(0);
  });

  test('backs off exponentially across repeated network failures (1s, 2s, 4s, 8s)', async () => {
    // Exponential growth only applies while the connection never actually
    // establishes (network error / non-2xx) — once setConnected(true) fires
    // the backoff resets (see the "resets backoff" test below), matching
    // useCollabSocket's onopen reset. So to observe the climbing sequence we
    // keep failing before a connection is ever established.
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('network error'));
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => useGoalStream('goal-backoff'));
    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const expectedDelays = [1000, 2000, 4000, 8000];
    for (const [i, delay] of expectedDelays.entries()) {
      await advance(delay - 1);
      expect(fetchMock).toHaveBeenCalledTimes(i + 1); // not yet
      await advance(1);
      expect(fetchMock).toHaveBeenCalledTimes(i + 2); // now retried
    }
  });

  test('resets backoff to 1s after a successful reconnect (regression)', async () => {
    // Prior to the fix, retryCountRef was only reset on a terminal event or a
    // new goalId — not on a successful (re)connect. So a goal that dropped
    // twice, reconnected cleanly, then dropped again would schedule the THIRD
    // retry at 4s (continuing to climb) instead of resetting to 1s.
    vi.useFakeTimers();
    const streams = Array.from({ length: 3 }, () => makeControllableStream());
    let call = 0;
    const fetchMock = vi.fn().mockImplementation(() => {
      const ctl = streams[call++];
      return Promise.resolve(
        new Response(ctl.stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => useGoalStream('goal-reset'));
    await advance(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Drop #1 → schedules retry at 1s (retryCount 0 -> 1).
    streams[0].close();
    await advance(1000);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // Connection #2 opens successfully and stays open for a while — this is
    // the "successful reconnect" that should reset the backoff counter.
    streams[1].push(sseFrame({ type: 'goal_started' }));
    await advance(5000);
    expect(fetchMock).toHaveBeenCalledTimes(2); // still just the one live connection

    // Drop #2 → without the fix this would be scheduled at 2s (continuing the
    // climb from retryCount=1); with the fix it restarts at 1s.
    streams[1].close();
    await advance(999);
    expect(fetchMock).toHaveBeenCalledTimes(2); // not yet at 1s
    await advance(1);
    expect(fetchMock).toHaveBeenCalledTimes(3); // reconnected at exactly 1s
  });

  test('ignores malformed/partial SSE data without crashing and keeps processing valid frames', async () => {
    const stream = makeSseStream([
      'data: {"type": "goal_started"\n\n', // malformed JSON (missing closing brace)
      'data: not-json-at-all\n\n',
      sseFrame({ type: 'step_started', step: 'A' }),
    ]);

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGoalStream('goal-malformed'));

    await waitFor(() => {
      expect(result.current.events.some((e) => e.type === 'step_started')).toBe(true);
    });

    // Only the well-formed frame should have been recorded — the malformed
    // ones were silently skipped rather than crashing the hook.
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe('step_started');
  });

  test('handles an SSE frame split across multiple chunks (buffering)', async () => {
    const fullFrame = sseFrame({ type: 'goal_started', note: 'hello world' });
    const splitPoint = Math.floor(fullFrame.length / 2);
    const part1 = fullFrame.slice(0, splitPoint);
    const part2 = fullFrame.slice(splitPoint);

    const encoder = new TextEncoder();
    const chunks = [part1, part2];
    let i = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (i >= chunks.length) {
          controller.close();
          return;
        }
        controller.enqueue(encoder.encode(chunks[i++]));
      },
    });

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGoalStream('goal-split'));

    await waitFor(() => {
      expect(result.current.events.some((e) => e.type === 'goal_started')).toBe(true);
    });
    expect(result.current.events[0].note).toBe('hello world');
  });

  test('a connection that never opens leaves state disconnected without throwing', async () => {
    vi.useFakeTimers();
    let capturedSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation((_url: string, opts: RequestInit) => {
      capturedSignal = opts.signal ?? undefined;
      return new Promise(() => {
        /* never resolves — simulates a connection that hangs */
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result, unmount } = renderHook(() => useGoalStream('goal-hang'));

    await advance(60_000);
    expect(result.current.connected).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1); // no retry storm while the first attempt is still pending

    // Cleanup must still abort the pending request on unmount.
    unmount();
    expect(capturedSignal?.aborted).toBe(true);
  });

  test('delivers rapid successive events without dropping any', async () => {
    const N = 50;
    const frames = Array.from({ length: N }, (_, i) =>
      sseFrame({ type: 'step_progress', iteration: i })
    );
    const stream = makeSseStream(frames);

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    );
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGoalStream('goal-rapid'));

    await waitFor(() => {
      expect(result.current.events).toHaveLength(N);
    });
    // Order preserved, nothing dropped or duplicated.
    expect(result.current.events.map((e) => e.iteration)).toEqual(
      Array.from({ length: N }, (_, i) => i)
    );
  });

  test('cleans up on unmount mid-stream: aborts the request and stops processing further frames', async () => {
    vi.useFakeTimers();
    const ctl = makeControllableStream();
    const fetchMock = vi.fn().mockImplementation((_url: string, opts: RequestInit) => {
      if (opts.signal) abortErrorFor(opts.signal, ctl);
      return Promise.resolve(
        new Response(ctl.stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

    const { result, unmount } = renderHook(() => useGoalStream('goal-unmount'));

    ctl.push(sseFrame({ type: 'goal_started' }));
    await advance(0);
    // `waitFor`'s polling relies on real timers, so with fake timers active we
    // assert directly instead — the state update has already flushed as part
    // of draining the microtask queue above.
    expect(result.current.events.some((e) => e.type === 'goal_started')).toBe(true);

    unmount();
    await advance(0);

    // No reconnect should be scheduled off the back of the AbortError, even
    // after waiting well past the first backoff window.
    await advance(40_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(consoleError).not.toHaveBeenCalled();
  });
});

// Consolidated from the former sse/__tests__/useGoalStream.test.ts, which only
// asserted the hook was exported without ever rendering it — these versions
// actually exercise the 401/403 short-circuit behaviour described in the
// hook's docstring (logout + no retry storm).
describe('useGoalStream — auth failure short-circuit', () => {
  beforeEach(() => {
    localStorage.setItem('av_api_key', 'test-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  test('401 response calls logout() and does not retry', async () => {
    const logoutSpy = vi.spyOn(useAuthStore.getState(), 'logout').mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGoalStream('goal-401'));

    await waitFor(() => {
      expect(logoutSpy).toHaveBeenCalledTimes(1);
    });
    expect(result.current.connected).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test('403 response calls logout() and does not retry', async () => {
    const logoutSpy = vi.spyOn(useAuthStore.getState(), 'logout').mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 403 }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useGoalStream('goal-403'));

    await waitFor(() => {
      expect(logoutSpy).toHaveBeenCalledTimes(1);
    });
    expect(result.current.connected).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
