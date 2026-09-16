/**
 * Tests for useRunSSE — workflow-run SSE hook (fetch/ReadableStream based).
 * Verifies parsed events, connection + error state, [DONE] termination,
 * malformed-frame tolerance, gating, and clearEvents.
 */
import { renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useRunSSE, type RunEvent } from './RunSSEStream';
import { useAuthStore } from '@/stores/auth';

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

const dataFrame = (evt: RunEvent, id?: string): string =>
  `${id ? `id: ${id}\n` : ''}data: ${JSON.stringify(evt)}\n\n`;

function streamResponse(frames: string[], status = 200): Response {
  return new Response(status === 200 ? makeSseStream(frames) : 'err', {
    status,
    headers: { 'Content-Type': status === 200 ? 'text/event-stream' : 'text/plain' },
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'run-key', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('useRunSSE', () => {
  test('does not connect when runId is missing', () => {
    const spy = vi.spyOn(globalThis, 'fetch');
    const { result } = renderHook(() => useRunSSE({ runId: null }));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.isConnected).toBe(false);
    expect(result.current.events).toEqual([]);
  });

  test('does not connect when disabled', () => {
    const spy = vi.spyOn(globalThis, 'fetch');
    renderHook(() => useRunSSE({ runId: 'r1', enabled: false }));
    expect(spy).not.toHaveBeenCalled();
  });

  test('streams to the run URL and accumulates typed events with a lastEvent', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([
        dataFrame({ event: 'step_started', step_id: 's1' }, '1'),
        dataFrame({ event: 'step_finished', step_id: 's1', status: 'ok' }, '2'),
      ]),
    );

    const { result } = renderHook(() => useRunSSE({ runId: 'run-42' }));

    await waitFor(() => expect(result.current.isConnected).toBe(true));
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events[0].event).toBe('step_started');
    expect(result.current.lastEvent?.event).toBe('step_finished');
    expect(String(spy.mock.calls[0][0])).toContain('/api/v1/runs/run-42/stream');
  });

  test('terminates the connection on a [DONE] sentinel', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([dataFrame({ event: 'step_started' }, '1'), 'data: [DONE]\n\n']),
    );

    const { result } = renderHook(() => useRunSSE({ runId: 'run-done' }));
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    await waitFor(() => expect(result.current.isConnected).toBe(false));
    // [DONE] is a control frame, never stored as an event.
    expect(result.current.events.map((e) => e.event)).toEqual(['step_started']);
  });

  test('skips malformed data frames but keeps valid ones', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([
        'data: {not valid json\n\n',
        dataFrame({ event: 'ok_event' }, '1'),
      ]),
    );

    const { result } = renderHook(() => useRunSSE({ runId: 'run-bad' }));
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].event).toBe('ok_event');
  });

  test('sets an error message when the response is not ok', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse([], 500));
    const { result } = renderHook(() => useRunSSE({ runId: 'run-500' }));
    await waitFor(() => expect(result.current.error).toBe('SSE failed: 500'));
    expect(result.current.isConnected).toBe(false);
  });

  test('clearEvents empties the accumulated events', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([dataFrame({ event: 'a' }, '1'), dataFrame({ event: 'b' }, '2')]),
    );
    const { result } = renderHook(() => useRunSSE({ runId: 'run-clear' }));
    await waitFor(() => expect(result.current.events).toHaveLength(2));

    act(() => result.current.clearEvents());
    expect(result.current.events).toEqual([]);
  });
});
