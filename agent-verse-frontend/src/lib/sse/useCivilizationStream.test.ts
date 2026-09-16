/**
 * Tests for useCivilizationStream — a fetch/ReadableStream-based SSE hook.
 *
 * We drive it with a real ReadableStream of SSE frames served through a mocked
 * globalThis.fetch (the same technique the repo uses for useGoalStream).
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useCivilizationStream } from './useCivilizationStream';
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

const sseFrame = (data: object): string => `data: ${JSON.stringify(data)}\n\n`;

function streamResponse(frames: string[]): Response {
  return new Response(makeSseStream(frames), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('useCivilizationStream', () => {
  test('does not fetch when the civilization id is null', () => {
    const spy = vi.spyOn(globalThis, 'fetch');
    const { result } = renderHook(() => useCivilizationStream(null));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toEqual([]);
  });

  test('connects and accumulates parsed events from the stream', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([
        sseFrame({ id: 'e1', civilization_id: 'c1', type: 'agent_spawned', payload: {}, ts: 't1' }),
        sseFrame({ id: 'e2', civilization_id: 'c1', type: 'goal_completed', payload: {}, ts: 't2' }),
      ]),
    );

    const { result } = renderHook(() => useCivilizationStream('c1'));

    await waitFor(() => expect(result.current.connected).toBe(true));
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events.map((e) => e.id)).toEqual(['e1', 'e2']);
    expect(result.current.events[0].type).toBe('agent_spawned');
  });

  test('deduplicates events with a repeated id', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([
        sseFrame({ id: 'dup', civilization_id: 'c1', type: 'a', payload: {}, ts: 't1' }),
        sseFrame({ id: 'dup', civilization_id: 'c1', type: 'a', payload: {}, ts: 't1' }),
        sseFrame({ id: 'unique', civilization_id: 'c1', type: 'b', payload: {}, ts: 't2' }),
      ]),
    );

    const { result } = renderHook(() => useCivilizationStream('c1'));
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events.map((e) => e.id)).toEqual(['dup', 'unique']);
  });

  test('invokes the onEvent callback for each event', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([sseFrame({ id: 'e1', civilization_id: 'c1', type: 'ping', payload: {}, ts: 't' })]),
    );
    const onEvent = vi.fn();
    renderHook(() => useCivilizationStream('c1', { onEvent }));
    await waitFor(() =>
      expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ id: 'e1', type: 'ping' })),
    );
  });

  test('a 401 response logs out and does not mark the stream connected', async () => {
    const logout = vi.fn();
    useAuthStore.setState({ logout });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('unauthorized', { status: 401, headers: { 'Content-Type': 'text/plain' } }),
    );

    const { result } = renderHook(() => useCivilizationStream('c1'));
    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toEqual([]);
  });
});
