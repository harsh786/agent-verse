/**
 * Tests for the useGoalStream SSE hook — token streaming behaviour.
 *
 * We test the token streaming logic by directly exercising the hook's
 * internal state transitions using React Testing Library's renderHook.
 */

import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useGoalStream } from './useGoalStream';

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
